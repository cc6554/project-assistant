/* Execute real TSX components with controlled React/browser boundaries. No DOM or microphone. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(filename, globals = {}, imports = {}) {
  let index = 0, hooks = [], effects = [];
  const react = {
    createElement: (type, props, ...children) => ({type, props: {...props, children}}),
    useRef: initial => { const i = index++; return hooks[i] ??= {current: initial}; },
    useState: initial => { const i = index++; if (!(i in hooks)) hooks[i] = typeof initial === 'function' ? initial() : initial;
      return [hooks[i], value => { hooks[i] = typeof value === 'function' ? value(hooks[i]) : value; }]; },
    useEffect: (effect, deps) => { const i = index++; const old = hooks[i];
      if (!old || !deps || deps.some((v, j) => v !== old.deps[j])) effects.push(() => {
        old?.cleanup?.(); hooks[i] = {deps, cleanup: effect()};
      }); },
  };
  const module = {exports: {}};
  const context = {module, exports: module.exports, React: react, console, URLSearchParams, Headers,
    require: name => name === 'react' ? react : (imports[name] ?? {default: () => null}), ...globals};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, filename), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React}
  }).outputText, context, {filename});
  return {render(props = {}) { index = 0; const tree = module.exports.default(props); const pending = effects; effects = []; pending.forEach(e => e()); return tree; },
    unmount() { hooks.forEach(h => h?.cleanup?.()); }, module};
}
function nodes(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  if (Array.isArray(tree)) return tree.flatMap(item => nodes(item, predicate));
  return [...(predicate(tree) ? [tree] : []), ...nodes(tree.props?.children, predicate)];
}

async function voiceTests() {
  let streams = [], recs = [], closed = 0, value = '已有';
  class Recognition { constructor() { recs.push(this); } start() {} stop() {} abort() {} }
  class AudioContext {
    createMediaStreamSource() { return {connect() {}}; }
    createAnalyser() { return {frequencyBinCount: 2, getByteFrequencyData: data => data.fill(0)}; }
    close() { closed++; return Promise.resolve(); }
  }
  const globals = {window: {SpeechRecognition: Recognition, AudioContext}, navigator: {mediaDevices: {
    getUserMedia: () => new Promise(resolve => streams.push(resolve))}}, requestAnimationFrame: () => 1,
    cancelAnimationFrame() {}, alert() {}};
  const component = harness('src/components/VoiceField.tsx', globals);
  const render = () => component.render({value, onChange: next => {value = next;}});
  const microphone = tree => nodes(tree, n => n.type === 'button')[0];
  microphone(render()).props.onClick(); render();
  recs[0].onresult({resultIndex: 0, results: [[{transcript: '一'}]]});
  // Another result arrives before React renders again.
  recs[0].onresult({resultIndex: 0, results: [[{transcript: '二'}]]});
  assert.equal(value, '已有一二');
  microphone(render()).props.onClick();
  let stopped = 0;
  streams.shift()({getTracks: () => [{stop: () => stopped++}]}); await tick();
  assert.equal(stopped, 1); assert.equal(closed, 0);
  microphone(render()).props.onClick(); render();
  streams.shift()({getTracks: () => [{stop: () => stopped++}]}); await tick();
  component.unmount();
  assert.equal(stopped, 2); assert.equal(closed, 1);
  const late = harness('src/components/VoiceField.tsx', globals);
  microphone(late.render({value: '', onChange() {}})).props.onClick(); late.unmount();
  streams.shift()({getTracks: () => [{stop: () => stopped++}]}); await tick();
  assert.equal(stopped, 3); assert.equal(closed, 1);
  console.log('PASS VoiceField: cumulative results, late stream after stop, cleanup on unmount, late stream after unmount');
}

async function appTests() {
  let interval;
  const sessions = [{id: 'first'}, {id: 'second'}];
  const api = {health: async () => ({}), listSessions: async () => sessions,
    getSession: async id => ({session_id: id, repos: [], interview_history: [], coach_history: []}),
    getModel: async () => ({options: [], current: null})};
  const app = harness('src/App.tsx', {window: {setInterval: callback => {interval = callback; return 1;}, clearInterval() {}},
    document: {documentElement: {dataset: {}}}, localStorage: {getItem() {}, setItem() {}}}, {'./api': {api}});
  app.render(); await tick(); let tree = app.render(); await tick();
  nodes(tree, n => n.props?.className === 'sess-item ')[0]?.props.onClick(); // select second
  tree = app.render(); await tick(); tree = app.render();
  assert.equal(nodes(tree, n => n.props?.className === 'sess-item active')[0].props.children[0].props.children[0], '（未命名岗位）');
  const selected = nodes(tree, n => String(n.props?.className).startsWith('sess-item'));
  assert.equal(selected[1].props.className, 'sess-item active');
  await interval(); tree = app.render();
  assert.equal(nodes(tree, n => String(n.props?.className).startsWith('sess-item'))[1].props.className, 'sess-item active');
  app.unmount();
  console.log('PASS App: selected session survives the five-second polling callback');
}

async function apiTests() {
  let replaced, saved, calls = [];
  const {module} = harness('src/api.ts', {window: {location: {hash: '#token=test-only-token', pathname: '/', search: ''}, history: {replaceState: (...args) => {replaced = args[2];}}},
    sessionStorage: {setItem: (key, value) => {saved = value;}, getItem: () => saved},
    fetch: async (url, init) => {calls.push({url, init}); return {ok: true, json: async () => ({})};}});
  await module.exports.api.listSessions(); await module.exports.api.agentTurnStream('one', 'test');
  assert.equal(replaced, '/'); assert.equal(saved, 'test-only-token');
  assert(calls.every(call => call.init.headers.get('X-Job-Radar-Token') === 'test-only-token'));
  assert(calls.every(call => !call.url.includes('token')));
  console.log('PASS API: fragment removed and authenticated ordinary/SSE requests');
}

async function configTests() {
  const configuration = {providers: {demo: {api_mode: 'chat_completions', base_url: 'https://example.test', api_key_env: 'ORIGINAL_ENV', timeout: 120, extra_body: {thinking: {type: 'enabled'}}}},
    tasks: {default: [{provider: 'demo', model: 'sample'}]}, models_in_use: ['sample']};
  let saved = [], notified = 0;
  const component = harness('src/pages/Config.tsx', {}, {'../api': {api: {
    getConfig: async () => configuration, getGithubToken: async () => ({configured: false, masked: ''}),
    putConfig: async value => saved.push(value)}}});
  const props = {onSaved: () => notified++};
  component.render(props); await tick();
  for (let i = 0; i < 2; i++) {
    const tree = component.render(props);
    const button = nodes(tree, n => n.type === 'button' && n.props.children.includes('保存配置'))[0];
    assert(button); await button.props.onClick(); await tick();
  }
  assert(saved.every(value => value.providers[0].api_key_env === 'ORIGINAL_ENV'));
  assert(saved.every(value => value.providers[0].extra_body.thinking.type === 'enabled'));
  assert.equal(notified, 2);
  component.unmount();
  console.log('PASS Config: consecutive saves preserve key environment and thinking options, notify model selector');
}

async function agentTests() {
  const content = 'full-content-'.repeat(100);
  const state = {session_id: 'test', repos: [], plan: {direction: 'test', gaps: [], phases: []}, agent_logs: [],
    agent_pending: {action_id: 'pending', kind: 'write_file', summary: 'write', tool_call_id: 'one', payload: {path: '/test/file', content, diff: 'full diff'}}};
  const component = harness('src/pages/Agent.tsx', {window: {setInterval: () => 1, clearInterval() {}}},
    {'../api': {api: {getSession: async () => state}}});
  const props = {sessionId: 'test', state, onState() {}};
  component.render(props); await tick();
  const tree = component.render(props);
  const text = JSON.stringify(tree);
  assert(text.includes(content)); assert(text.includes('full diff'));
  assert(nodes(tree, n => n.type === 'button' && JSON.stringify(n.props.children).includes('同意执行')).length);
  component.unmount();
  console.log('PASS Agent: persisted approval restored with complete content and diff');
}

(async () => {await voiceTests(); await appTests(); await apiTests(); await configTests(); await agentTests();})().catch(error => {console.error(error); process.exitCode = 1;});
