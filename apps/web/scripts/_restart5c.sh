#!/bin/sh
# restart the fixtures server on 3110 (own processes only)
for p in $(lsof -tiTCP:3110 -sTCP:LISTEN); do kill $p; done
sleep 1
ABB_WEB_DATA_SOURCE=fixtures ABB_WEB_ALLOW_FIXTURES=1 nohup pnpm exec next start --port 3110 > /private/tmp/abb-5c-server.log 2>&1 &
sleep 5
