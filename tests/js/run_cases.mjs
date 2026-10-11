// node tests/js/run_cases.mjs <cases.json> <schema.json>: prints, for every case, the problems docs/validator.js returns.
import { readFileSync } from "node:fs";
import { problems } from "../../docs/validator.js";

const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));
const data = JSON.parse(readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify(cases.map((c) => problems(c.doc, data))));
