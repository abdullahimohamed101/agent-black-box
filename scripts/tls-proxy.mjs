// A throwaway TLS terminator for the https auth e2e (scripts/auth-https-e2e.sh). `next start` has no https mode, so
// this puts a self-signed certificate in front of it: https://localhost:<listen> -> http://127.0.0.1:<target>.
// DEVELOPMENT/TEST ONLY: it forwards everything and checks nothing.
//   node scripts/tls-proxy.mjs <listen-port> <target-port> <key.pem> <cert.pem>
import fs from "node:fs";
import http from "node:http";
import https from "node:https";

const [listen, target, keyFile, certFile] = process.argv.slice(2);
if (!listen || !target || !keyFile || !certFile) {
  console.error("usage: tls-proxy.mjs <listen-port> <target-port> <key> <cert>");
  process.exit(2);
}
const server = https.createServer(
  { key: fs.readFileSync(keyFile), cert: fs.readFileSync(certFile) },
  (req, res) => {
    const upstream = http.request(
      { host: "127.0.0.1", port: Number(target), method: req.method, path: req.url, headers: req.headers },
      (up) => {
        res.writeHead(up.statusCode ?? 502, up.headers);
        up.pipe(res);
      },
    );
    upstream.on("error", () => {
      if (!res.headersSent) res.writeHead(502);
      res.end();
    });
    req.pipe(upstream);
  },
);
server.listen(Number(listen), "127.0.0.1");
