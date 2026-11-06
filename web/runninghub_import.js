import {ZIP_LIMIT} from './transfer_core.js';

const manifestName = 'manifest.json';
const assetPath = /^assets\/([0-9a-f]{64})(\.[a-z0-9]{2,5})$/;
const imageTypes = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.webp': 'image/webp', '.bmp': 'image/bmp', '.tif': 'image/tiff', '.tiff': 'image/tiff'};
const videoTypes = {'.mp4': 'video/mp4', '.mov': 'video/quicktime', '.mkv': 'video/x-matroska',
  '.webm': 'video/webm', '.gif': 'image/gif', '.avi': 'video/x-msvideo', '.m4v': 'video/mp4'};
const numberFields = new Set(['force_rate', 'custom_width', 'custom_height', 'frame_load_cap',
  'skip_first_frames', 'select_every_nth']);
const decoder = new TextDecoder('utf-8', {fatal: true});

export const isRunningHub = hostname => /(^|\.)runninghub\.(ai|cn)$/i.test(hostname);

async function readView(blob, start, length) {
  return new DataView(await blob.slice(start, start + length).arrayBuffer());
}

// H3 v1 writers use ZIP_STORED, fixed ASCII paths and no descriptors or comments.
// Read local entries as Blob slices so large videos do not need to be inflated.
async function storedEntries(blob) {
  if (blob.size < 22 || blob.size > ZIP_LIMIT) throw new Error('交接 ZIP 大小无效');
  const trailer = await readView(blob, blob.size - 22, 22);
  const count = trailer.getUint16(10, true);
  const central = trailer.getUint32(16, true);
  if (trailer.getUint32(0, true) !== 0x06054b50 || trailer.getUint32(4, true) !== 0 ||
      trailer.getUint16(8, true) !== count || trailer.getUint16(20, true) !== 0 ||
      count < 2 || count > 17 || central + trailer.getUint32(12, true) !== blob.size - 22) {
    throw new Error('交接 ZIP 不完整，请使用发送节点生成的原包');
  }
  const entries = new Map();
  let offset = 0;
  while (offset < central) {
    if (offset + 30 > central || entries.size >= count) throw new Error('交接 ZIP 文件表无效');
    const header = await readView(blob, offset, 30);
    const nameSize = header.getUint16(26, true);
    const dataSize = header.getUint32(22, true);
    const start = offset + 30 + nameSize + header.getUint16(28, true);
    const end = start + dataSize;
    if (header.getUint32(0, true) !== 0x04034b50 || header.getUint16(8, true) !== 0 ||
        (header.getUint16(6, true) & ~0x800) !== 0 || header.getUint32(18, true) !== dataSize ||
        nameSize < 1 || nameSize > 100 || end > central) {
      throw new Error('请使用发送节点生成的原包，RunningHub 导入需要 H3 原始 ZIP 格式');
    }
    const name = decoder.decode(await blob.slice(offset + 30, offset + 30 + nameSize).arrayBuffer());
    if ((name !== manifestName && !assetPath.test(name)) || entries.has(name)) {
      throw new Error('交接 ZIP 包含无效或重复的文件名');
    }
    entries.set(name, blob.slice(start, end));
    offset = end;
  }
  if (entries.size !== count) throw new Error('交接 ZIP 文件数量不符');
  return entries;
}

function validateManifest(manifest, entries) {
  if (manifest?.format !== 'h3-transfer' || manifest.version !== 1 ||
      typeof manifest.prompt !== 'string' || manifest.prompt.length > 200000) {
    throw new Error('交接包格式无效');
  }
  const assets = new Map(Object.entries(manifest.assets ?? {}));
  if (!assets.size || entries.size !== assets.size + 1) throw new Error('交接包素材清单无效');
  for (const [key, asset] of assets) {
    const parts = assetPath.exec(key);
    const types = asset?.kind === 'image' ? imageTypes : asset?.kind === 'video' ? videoTypes : {};
    if (!parts || !types[parts[2]] || asset.sha256 !== parts[1] || !entries.has(key) ||
        entries.get(key).size !== asset.size) throw new Error('交接包素材信息无效');
  }
  const checkRef = (key, kind) => {
    if (assets.get(key)?.kind !== kind) throw new Error('交接包素材引用无效');
  };
  checkRef(manifest.reference_video?.asset, 'video');
  checkRef(manifest.first_pass_video?.asset, 'video');
  if (!Array.isArray(manifest.reference_images) || manifest.reference_images.length !== 6) {
    throw new Error('交接包需要包含六个参考图槽位');
  }
  manifest.reference_images.forEach((item, index) => {
    if (item?.slot !== index + 1 || typeof item.enabled !== 'boolean') throw new Error('参考图槽位无效');
    if (item.enabled || item.asset !== null) checkRef(item.asset, 'image');
  });
  const loader = manifest.reference_video.loader;
  if (!loader || Array.isArray(loader) || typeof loader !== 'object' || Object.entries(loader).some(
    ([key, value]) => key === 'format' ? typeof value !== 'string' || value.length > 100 :
      !numberFields.has(key) || typeof value !== 'number' || !(value >= 0 && value <= 1e9))) {
    throw new Error('原参考视频读取参数无效');
  }
  const settings = manifest.common_settings;
  const resize = settings?.reference_video_resize;
  if (!Number.isInteger(settings?.reference_short_edge) || settings.reference_short_edge < 1 ||
      settings.reference_short_edge > 32768 || !resize || Object.keys(resize).length !== 3 ||
      !['nearest-exact', 'bilinear', 'area', 'bicubic', 'lanczos'].includes(resize.upscale_method) ||
      typeof resize.megapixels !== 'number' || !(resize.megapixels > 0 && resize.megapixels <= 100) ||
      !Number.isInteger(resize.resolution_steps) || resize.resolution_steps < 1 || resize.resolution_steps > 4096) {
    throw new Error('交接包尺寸设置无效');
  }
  return assets;
}

async function uploadAsset(fetchApi, name, blob, mime) {
  const form = new FormData();
  form.append('image', new Blob([blob], {type: mime}), name);
  form.append('type', 'input');
  const response = await fetchApi('/upload/image', {method: 'POST', body: form});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`RunningHub 素材上传失败（HTTP ${response.status}）${result.error || result.msg || ''}`);
  }
  const path = result.subfolder ? `${result.subfolder}/${result.name}` : result.name;
  if (typeof result.name !== 'string' || !/^[a-zA-Z0-9_.-]+(?:\/[a-zA-Z0-9_.-]+)*$/.test(path) ||
      path.split('/').some(segment => segment === '.' || segment === '..')) {
    throw new Error('RunningHub 未返回有效的素材文件名');
  }
  return path;
}

export async function importOnRunningHub(bytes, fetchApi, validateReceipt) {
  const blob = bytes instanceof Blob ? bytes : new Blob([bytes]);
  const entries = await storedEntries(blob);
  const manifestFile = entries.get(manifestName);
  if (!manifestFile || manifestFile.size > 1024 * 1024) throw new Error('缺少交接清单或清单过大');
  const manifest = JSON.parse(await manifestFile.text());
  const assets = validateManifest(manifest, entries);
  const reference = manifest.reference_video;
  const firstPass = manifest.first_pass_video.asset;
  const images = manifest.reference_images;
  const selected = new Set([reference.asset, firstPass, ...images.filter(item => item.enabled).map(item => item.asset)]);
  const receipt = pathFor => ({
    import_id: '', prompt: manifest.prompt,
    reference_video: {path: pathFor(reference.asset), loader: reference.loader},
    first_pass_video: {path: pathFor(firstPass)},
    reference_images: images.map(item => ({slot: item.slot, enabled: item.enabled,
      path: item.enabled ? pathFor(item.asset) : null})),
    common_settings: manifest.common_settings,
  });
  const fileName = key => `h3-${key.slice(7)}`;
  validateReceipt(receipt(fileName));
  for (const [key, asset] of assets) {
    const digest = await crypto.subtle.digest('SHA-256', await entries.get(key).arrayBuffer());
    const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
    if (hex !== asset.sha256) throw new Error('交接包素材内容校验失败');
  }
  const paths = new Map();
  for (const key of selected) {
    const asset = assets.get(key);
    const types = asset.kind === 'image' ? imageTypes : videoTypes;
    paths.set(key, await uploadAsset(fetchApi, fileName(key), entries.get(key), types[assetPath.exec(key)[2]]));
  }
  return receipt(key => paths.get(key));
}
