const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "src/agent_coach/web_static/index.html"), "utf8");
const app = fs.readFileSync(path.join(root, "src/agent_coach/web_static/app.js"), "utf8");
const css = fs.readFileSync(path.join(root, "src/agent_coach/web_static/styles.css"), "utf8");

function hasId(id) {
  return new RegExp(`\\bid="${id}"`).test(html);
}

[
  "planPaperProjects",
  "planProjectUrl",
  "planProjectFileInput",
  "planProjectUpload",
  "planProjectLocalImport",
  "planProjectResourceList",
  "planProjectRegister",
].forEach((id) => assert.ok(hasId(id), `plan page should expose ${id}`));

assert.match(html, /id="planPaperProjects" checked/);
assert.match(html, /查找相关高影响力论文的开源实现/);
assert.match(html, /核对论文来源、代码关联及影响力依据；不足时标为待核实。/);
assert.match(app, /include_paper_implementations=true/);
assert.match(app, /register_project/);
assert.match(app, /source\.paths/);
assert.match(app, /submitOwnProjectPage/);
assert.match(app, /不要触发外部项目检索或生成、重算学习计划/);
assert.match(app, /handlePlanProjectUploads/);
assert.match(app, /openLocalImport\("planProject"\)/);
assert.match(app, /request\("\/imports\/local"/);
assert.match(app, /project\.project_key \|\| project\.projectKey \|\| project\.id/);
assert.match(app, /projectDisplayTitle/);
assert.match(app, /projectIsLocalOnly/);
assert.match(css, /\.profile-table \.table-header-lines span \{ display: block/);
assert.match(css, /\.profile-table th:nth-child\(2\).*min-width: 104px/);

console.log("plan/project DOM contract: paper search, independent project registration, stable local identity, and profile table layout passed");
