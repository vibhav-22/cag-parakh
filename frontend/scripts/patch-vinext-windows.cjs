const fs = require("node:fs");
const path = require("node:path");

if (process.platform !== "win32") process.exit(0);

const target = path.join(
  __dirname,
  "..",
  "node_modules",
  "vinext",
  "dist",
  "server",
  "static-file-cache.js",
);
const before = "relativePath: path.relative(base, batch[j]),";
const after = 'relativePath: path.relative(base, batch[j]).split(path.sep).join("/"),';

if (!fs.existsSync(target)) {
  throw new Error("Vinext is not installed. Run npm install first.");
}

const source = fs.readFileSync(target, "utf8");
if (source.includes(after)) {
  console.log("[desktop] Vinext Windows static-asset patch already applied.");
  process.exit(0);
}
if (!source.includes(before)) {
  throw new Error(
    "The installed Vinext static-file cache has changed; review the Windows asset patch before continuing.",
  );
}

fs.writeFileSync(target, source.replace(before, after), "utf8");
console.log("[desktop] Applied Vinext Windows static-asset path patch.");
