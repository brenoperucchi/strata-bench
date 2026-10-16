// node tests/js/run_cases.mjs <cases.json> <schema.json>: prints, for every case, the problems docs/validator.js returns.
import { readFileSync } from "node:fs";
import { gate, problems } from "../../docs/validator.js";

const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));
const data = JSON.parse(readFileSync(process.argv[3], "utf8"));
console.log(JSON.stringify(cases.map((c) => (c.gate ? gate(c.doc, data) : problems(c.doc, data)))));     // a case with "gate": true also gets the profile rule
