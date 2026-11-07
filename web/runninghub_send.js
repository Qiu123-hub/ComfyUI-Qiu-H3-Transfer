const BASE = '/h3_transfer/runninghub';
const LEVELS = Array.from({length: 11}, (_, index) => (1 + index / 10).toFixed(1));
const INSTANCES = {default: '24 GB', plus: '48 GB', ultra: '84 GB'};
const WAITING = new Set(['preparing', 'uploading', 'submitting', 'running']);

async function request(api, path, body) {
  const options = body ? {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  } : {cache: 'no-store'};
  const response = await api.fetchApi(`${BASE}${path}`, options);
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

async function configure(api, settings) {
  return new Promise(resolve => {
    const dialog = dialogWith('RunningHub 二采设置');
    const key = document.createElement('input');
    key.type = 'password';
    key.autocomplete = 'off';
    key.placeholder = settings.configured ? '已保存，留空继续使用' : '在这里粘贴 API Key';
    key.style.width = 'min(420px, 65vw)';
    const keyLabel = document.createElement('label');
    keyLabel.append('API Key ', key);
    const instance = document.createElement('select');
    for (const [value, text] of Object.entries(INSTANCES)) instance.add(new Option(text, value));
    instance.value = settings.instanceType;
    const instanceLabel = document.createElement('label');
    instanceLabel.append('云端显存 ', instance);
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
        await request(api, '/settings', {apiKey: key.value, instanceType: instance.value});
        key.value = '';
        resolve(true);
        dialog.close();
      } catch (error) {
        message.textContent = error.message;
        save.disabled = false;
      }
    };
    dialog.append(keyLabel, document.createElement('p'), instanceLabel, message, save, cancel);
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

export function addRunningHubSender(node, api, button) {
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
    resolution.value = savedLevel();
    this.expandToFitContent?.();
  };

  button(node, '二采设置', async () => configure(api, await request(api, '/settings')));
  button(node, '发送并运行二采', async () => {
    const id = node.properties?.h3_package_id;
    if (!id) throw new Error('请先运行一采，等发送节点显示已打包。');
    const megapixels = Number(resolution.value);
    const settings = await request(api, '/settings');
    if (!settings.configured && !await configure(api, settings)) return;

    const path = `/job/${encodeURIComponent(id)}`;
    let job = await request(api, path, {megapixels});
    while (WAITING.has(job.state)) {
      if (node.properties?.h3_package_id === id) {
        node.title = `H3 ${job.message}${job.taskId ? ` #${job.taskId}` : ''}`;
        node.graph?.setDirtyCanvas(true, true);
      }
      await new Promise(resolve => setTimeout(resolve, 3000));
      job = await request(api, `${path}?megapixels=${megapixels}`);
    }
    if (job.state !== 'done') throw new Error(`${job.message || '二采未完成。'}${job.taskId ? `\n任务 ID ${job.taskId}` : ''}`);
    if (node.properties?.h3_package_id === id) node.title = `H3 二采已保存 #${job.taskId}`;
    showVideo(api, job);
  });
}
