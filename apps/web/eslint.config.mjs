import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  globalIgnores([".next/**", "out/**", "next-env.d.ts", "coverage/**"]),
  {
    rules: {
      // Trace payloads are untrusted; they must only ever be rendered as text.
      "react/no-danger": "error",
    },
  },
]);
