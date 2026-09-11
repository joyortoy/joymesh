import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = resolve(
  frontendRoot,
  "../src/joymesh/connectors/catalogue",
);
const output = join(frontendRoot, "app/connector-catalogue.generated.json");

if (!existsSync(sourceRoot)) {
  if (!existsSync(output)) {
    throw new Error("Backend connector catalogue and generated snapshot are both unavailable");
  }
  process.exit(0);
}

const connectors = readdirSync(sourceRoot)
  .filter((name) => name.endsWith(".yaml"))
  .sort()
  .map((name) => ({
    tier: "terminal",
    open_source: false,
    executable_names: [],
    installation_options: [],
    authentication_methods: [],
    provider_modes: [],
    experimental: true,
    blocked_reason: null,
    ...JSON.parse(readFileSync(join(sourceRoot, name), "utf8")),
  }))
  .sort((left, right) => left.harness_id.localeCompare(right.harness_id));

mkdirSync(dirname(output), { recursive: true });
writeFileSync(output, `${JSON.stringify(connectors, null, 2)}\n`);
