const PLATFORMS = {'RunningHub': 'runninghub', '晨羽智云': 'chenyu'};
const LEVELS = Array.from({length: 11}, (_, index) => (1 + index / 10).toFixed(1));
const INSTANCES = {default: '24 GB', plus: '48 GB', ultra: '84 GB'};
const WAITING = new Set(['preparing', 'uploading', 'submitting', 'running']);

async function request(api, platform, path, body) {
  const options = body ? {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  } : {cache: 'no-store'};
  const response = await api.fetchApi(`/h3_transfer/${PLATFORMS[platform]}${path}`, options);
  if (response.status === 403) throw new Error('请从本机 localhost 或 127.0.0.1 打开 ComfyUI。');
  if (response.status === 404) throw new Error('找不到本地二采接口，请安装新版节点后重启 ComfyUI。');
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '本地二采接口请求失败。');
  return data;
}

function dialogWith(title) {
  const dialog = document.createElement('dialog');
  const heading = document.createElement('h3');
  heading.textContent = title;
  dialog.append(heading);
  dialog.addEventListener('close', () => dialog.remove(), {once: true});
  return dialog;
}

async function configure(api, platform, settings) {
  return new Promise(resolve => {
    const dialog = dialogWith(`${platform} 二采设置`);
    const key = document.createElement('input');
    key.type = 'password';
    key.autocomplete = 'off';
    key.placeholder = settings.configured ? '已保存，留空继续使用' : '在这里粘贴 API Key';
    key.style.width = 'min(420px, 65vw)';
    const keyLabel = document.createElement('label');
    keyLabel.append('API Key ', key);
    dialog.append(keyLabel);
    let instance;
    let revision;
    if (platform === 'RunningHub') {
      instance = document.createElement('select');
      for (const [value, text] of Object.entries(INSTANCES)) instance.add(new Option(text, value));
      instance.value = settings.instanceType;
      const instanceLabel = document.createElement('label');
      instanceLabel.append('云端显存 ', instance);
      dialog.append(document.createElement('p'), instanceLabel);
    } else {
      revision = document.createElement('input');
      revision.value = settings.revisionId;
      revision.spellcheck = false;
      revision.size = 31;
      const revisionLabel = document.createElement('label');
      revisionLabel.append('版本 ID ', revision);
      const hint = document.createElement('p');
      hint.textContent = '参考图不足六张时，空槽会使用内置默认图。';
      dialog.append(document.createElement('p'), revisionLabel, hint);
    }
    const message = document.createElement('p');
    const save = document.createElement('button');
    save.textContent = '保存设置';
    const cancel = document.createElement('button');
    cancel.textContent = '取消';
    cancel.onclick = () => dialog.close();
    dialog.addEventListener('close', () => resolve(false), {once: true});
    save.onclick = async () => {
      save.disabled = true;
      try {
        const body = {apiKey: key.value};
        if (instance) body.instanceType = instance.value;
        if (revision) body.revisionId = revision.value;
        await request(api, platform, '/settings', body);
        key.value = '';
        resolve(true);
        dialog.close();
      } catch (error) {
        message.textContent = error.message;
        save.disabled = false;
      }
    };
    dialog.append(message, save, cancel);
    document.body.append(dialog);
    dialog.showModal();
    key.focus();
  });
}

function showVideo(api, job) {
  const dialog = dialogWith('二采已完成');
  const path = document.createElement('p');
  path.textContent = `output/${job.subfolder}/${job.filename}`;
  const task = document.createElement('p');
  task.textContent = `任务 ID ${job.taskId}`;
  const link = document.createElement('a');
  const query = new URLSearchParams({filename: job.filename, subfolder: job.subfolder, type: 'output'});
  link.href = api.apiURL(`/view?${query}`);
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = '打开成片';
  const close = document.createElement('button');
  close.textContent = '关闭';
  close.onclick = () => dialog.close();
  dialog.append(path, task, link, close);
  document.body.append(dialog);
  dialog.showModal();
}

export function addCloudSender(node, api, button) {
  const platformOptions = Object.keys(PLATFORMS);
  const savedPlatform = () => platformOptions.includes(node.properties?.h3_cloud_platform)
    ? node.properties.h3_cloud_platform : 'RunningHub';
  const cloud = node.addWidget('combo', '二采平台', savedPlatform(), value => {
    node.properties ||= {};
    node.properties.h3_cloud_platform = value;
  }, {values: platformOptions, serialize: false});
  cloud.serialize = false;
  cloud.serializeValue = () => undefined;
  const savedLevel = () => {
    const value = String(node.properties?.h3_rh_megapixels ?? '1.5');
    return LEVELS.includes(value) ? value : '1.5';
  };
  const resolution = node.addWidget('combo', '二采分辨率（百万像素）', savedLevel(), value => {
    node.properties ||= {};
    node.properties.h3_rh_megapixels = value;
  }, {values: LEVELS, serialize: false});
  resolution.serialize = false;
  resolution.serializeValue = () => undefined;
  const configured = node.onConfigure;
  node.onConfigure = function () {
    configured?.apply(this, arguments);
    cloud.value = savedPlatform();
    resolution.value = savedLevel();
    this.expandToFitContent?.();
  };

  button(node, '二采设置', async () => {
    const platform = cloud.value;
    return configure(api, platform, await request(api, platform, '/settings'));
  });
  button(node, '发送并运行二采', async () => {
    const id = node.properties?.h3_package_id;
    if (!id) throw new Error('请先运行一采，等发送节点显示已打包。');
    const megapixels = Number(resolution.value);
    const platform = cloud.value;
    const settings = await request(api, platform, '/settings');
    if (!settings.configured && !await configure(api, platform, settings)) return;

    const path = `/job/${encodeURIComponent(id)}`;
    let job = await request(api, platform, path, {megapixels});
    const query = new URLSearchParams({megapixels});
    if (job.revisionId) query.set('revisionId', job.revisionId);
    while (WAITING.has(job.state)) {
      if (node.properties?.h3_package_id === id) {
        node.title = `H3 ${job.message}${job.taskId ? ` #${job.taskId}` : ''}`;
        node.graph?.setDirtyCanvas(true, true);
      }
      await new Promise(resolve => setTimeout(resolve, 3000));
      job = await request(api, platform, `${path}?${query}`);
    }
    if (job.state !== 'done') throw new Error(`${job.message || '二采未完成。'}${job.taskId ? `\n任务 ID ${job.taskId}` : ''}`);
    if (node.properties?.h3_package_id === id) node.title = `H3 二采已保存 #${job.taskId}`;
    showVideo(api, job);
  });
}
