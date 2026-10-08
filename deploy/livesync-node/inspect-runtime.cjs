// Inspect the node's runtime storage: print key names and any CouchDB URL with
// credentials stripped. Used to find the active connection when the settings
// file itself carries none.
const fs = require("fs");
const path = require("path");

const dir = process.argv[2];
for (const f of fs.readdirSync(dir)) {
  const p = path.join(dir, f);
  if (!fs.statSync(p).isFile()) continue;
  const text = fs.readFileSync(p, "utf8");
  console.log(`== ${f} (${text.length} bytes)`);
  let json;
  try {
    json = JSON.parse(text);
  } catch (e) {
    const urls = [...new Set(text.match(/https?:\/\/[^\s"'\\]+/g) || [])];
    console.log(`   not-json, urls=${urls.length}`);
    for (const u of urls.slice(0, 5)) console.log(`   URL ${u.replace(/\/\/[^@\s"]*@/, "//<userinfo>@")}`);
    continue;
  }
  const walk = (o, prefix) => {
    for (const [k, v] of Object.entries(o || {})) {
      if (typeof v === "string") {
        if (/http/i.test(v)) console.log(`   key=${prefix}${k} url=${v.replace(/\/\/[^@\s"]*@/, "//<userinfo>@")}`);
        else if (/(user|name|db|vault|device)/i.test(k)) console.log(`   key=${prefix}${k} value=${v.slice(0, 40)} (len ${v.length})`);
        else console.log(`   key=${prefix}${k} (len ${v.length})`);
      } else if (v && typeof v === "object") {
        walk(v, `${prefix}${k}.`);
      } else {
        console.log(`   key=${prefix}${k} = ${JSON.stringify(v)}`);
      }
    }
  };
  walk(json, "");
}
