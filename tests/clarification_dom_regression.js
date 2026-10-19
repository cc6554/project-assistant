const assert = require("assert");
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const app = fs.readFileSync(path.join(root, "src/agent_coach/web_static/app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "src/agent_coach/web_static/index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "src/agent_coach/web_static/styles.css"), "utf8");

function functionBody(source, name) {
  const start = source.indexOf(`function ${name}`);
  assert.ok(start >= 0, `${name} should exist`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open, index + 1);
    }
  }
  throw new Error(`unterminated ${name}`);
}

assert.match(html, /id="clarificationBackdrop"/);
assert.match(html, /id="clarificationForm"/);
assert.match(html, /id="clarificationQuestion"/);
assert.match(html, /id="clarificationAnswer"[^>]*textarea|<textarea[^>]*id="clarificationAnswer"/);
assert.match(html, /id="clarificationRecovery"/);
assert.match(css, /\.clarification-modal/);
assert.match(css, /@media \(max-width: 680px\)/);

// The clarification envelope is deliberately narrower than a normal message.
// These client correlation hints were previously rejected by the strict
// endpoint contract and must never be sent from the browser.
assert.doesNotMatch(app, /question_id\s*:/);
assert.doesNotMatch(app, /request_id\s*:/);
assert.match(app, /\$\("clarificationAnswer"\)\.addEventListener\("input"/);
assert.match(app, /state\.clarification\.completed/);
assert.match(app, /metadata\.confirmed === true/);
assert.match(app, /String\(session\.id \|\| ""\) !== id/);

const feedback = functionBody(app, "clarificationFeedbackText");
assert.doesNotMatch(feedback, /visibleMessages/);
assert.match(feedback, /result\.message/);
assert.match(feedback, /result\.assistant_text/);

const finish = functionBody(app, "finishClarificationJob");
assert.match(finish, /clarificationSnapshotFor\(session, id\)/);
assert.match(finish, /metadata\.confirmed === true/);
assert.match(finish, /blocked/);
assert.match(finish, /awaitingConfirmation/);

const templateAction = functionBody(app, "draftWorkflowPrompt");
assert.doesNotMatch(templateAction, /composerInput"\)\.value\s*=/);
assert.match(templateAction, /setPendingWorkflowAction/);
assert.match(app, /准备对话引导会记录动作上下文/);

console.log("clarification DOM contract and stale-result guards passed");
