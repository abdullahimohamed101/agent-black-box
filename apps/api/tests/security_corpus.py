"""Benign, realistic agent activity. The security detectors must report nothing for any of it
(Phase 13 gate 4). Add to it whenever a false positive is found; never remove an entry to pass.
One entry per line."""

BENIGN_COMMANDS = tuple(
    line
    for line in r"""
npm install
npm ci
npm test
npm run build
npm run lint -- --fix
npm run dev
npx prettier --check .
npx tsc --noEmit
pnpm install --frozen-lockfile
pnpm --filter web test
yarn install
yarn build
pip install -r requirements.txt
pip install -e .[dev]
uv sync
uv run pytest -q
uv run ruff check .
uv run mypy
poetry install
cargo build --release
cargo test
go build ./...
go test ./... -race
make test
make build
make clean
make lint
mvn -q test
gradle build
bundle install
bundle exec rspec
dotnet test
python -m pytest tests/ -x -q
pytest -k 'drop_database or truncate'
python manage.py migrate
python manage.py test
python -m unittest discover -s tests -t .
python scripts/build.py
python -c 'print(1)'
node index.js
node --test
tsc -p tsconfig.json
ruff format .
black --check src
eslint src --ext .ts,.tsx
jest --coverage
vitest run
alembic upgrade head
alembic revision -m 'add table'
prisma migrate dev
git status
git status --short
git diff
git diff --stat
git diff HEAD~1
git log --oneline -n 20
git add -A
git add src/app.py
git add .env.example
git commit -m 'fix: handle empty input'
git commit -m 'docs: explain DROP TABLE migration'
git commit -m 'chore: remove .env from the example'
git commit --amend --no-edit
git checkout -b feature/oauth-refresh
git checkout main
git switch main
git pull --rebase origin main
git fetch --all --prune
git merge main
git rebase main
git stash
git stash pop
git push
git push -u origin feature/oauth-refresh
git push origin feature/main-menu
git push origin HEAD
git push --force-with-lease origin feature/x
git push --tags origin
git branch -d feature/old
git branch -D scratch
git reset --hard
git reset --hard origin/main
git reset HEAD~1
git reset --soft HEAD~1
git clean -n
git clean -fd
git cherry-pick abc1234
git tag v1.2.3
git remote -v
git show HEAD
git blame src/app.py
git ls-files
git rev-parse HEAD
git config user.name
git diff --no-color -- . ':(exclude,icase,glob)**/.env' ':(exclude,icase,glob)**/*.pem'
ls
ls -la
ls -la src/
pwd
cat README.md
cat package.json
cat src/main.py
head -n 50 logs/app.log
tail -n 200 logs/server.log
tail -f logs/dev.log
wc -l src/*.py
grep -rn 'TODO' src
grep -r 'DATABASE_URL' .env.example
grep -rn "\.env" docs
rg 'def main' src
rg -n 'password' --glob '!*.lock' src
find . -name '*.py' -not -path './.venv/*'
find src -type f -name '*.ts'
tree -L 2
du -sh node_modules
which python
env | grep PATH
cat .env.example
cat .env.sample
cat docs/credentials.md
cat src/secrets.py
cat ~/.ssh/id_rsa.pub
ls -la ~/.ssh
ls .env
test -f .env && echo present
mkdir -p build/out
touch src/new_file.py
cp .env.example .env
mv old.py new.py
rm old.log
rm -rf node_modules
rm -rf build dist
rm -rf .pytest_cache
rm -rf ./build
rm -rf /tmp/work-dir
rm -rf coverage/*
rm -r *.pyc
chmod +x scripts/run.sh
chmod 755 bin/tool
chmod -R 755 ./bin
sed -i 's/foo/bar/' src/app.py
tar czf dist.tgz dist
unzip -q archive.zip -d out
echo hello > out.txt
echo 'rm -rf /' > notes.txt
diff a.txt b.txt
sort -u words.txt
curl -s localhost:8000/health
curl -s http://127.0.0.1:3000/api/items
curl -sf http://localhost:5173
wget -q http://localhost:8080/file
curl -X POST http://localhost:8000/v1/events -d @payload.json
nc -z localhost 5432
ping -c 1 localhost
ssh -V
docker build -t app .
docker run --rm app pytest
docker compose up -d
docker compose logs web
docker ps
docker images
kubectl get pods
terraform plan
helm list
psql -c 'select 1'
psql -c 'SELECT count(*) FROM users'
psql -c "delete from sessions where expired"
sqlite3 app.db '.tables'
sqlite3 app.db 'select * from t'
redis-cli ping
redis-cli get key
bash -c 'npm test'
sh -c 'echo ok'
sudo apt-get update
time make test
FOO=bar npm run dev
cd src && ls
cd /tmp && ls
export NODE_ENV=test && npm test
set -e; npm ci; npm test
for f in *.py; do black $f; done
if [ -f .env ]; then echo yes; fi
[ -d node_modules ] || npm install
echo $HOME
echo done # cleanup rm -rf /
""".splitlines()
    if line
)

BENIGN_PATHS = tuple(
    line
    for line in r"""
README.md
package.json
pnpm-lock.yaml
tsconfig.json
pyproject.toml
uv.lock
src/main.py
src/app/page.tsx
src/lib/api/client.ts
tests/test_login.py
tests/fixtures/users.json
docs/architecture.md
docs/credentials.md
src/secrets.py
src/auth/credentials.py
src/config/env.ts
src/env.py
environment.yml
.envrc
.env.example
.env.sample
.env.template
.env.dist
config/.env.example
.gitignore
.github/workflows/ci.yml
Dockerfile
docker-compose.yml
Makefile
scripts/run.sh
id_rsa.pub
~/.ssh/id_ed25519.pub
~/.ssh/known_hosts
~/.ssh/config
cacert.pem
project/.npmrc
.prettierrc
.eslintrc.json
node_modules/left-pad/index.js
build/output.js
dist/bundle.js
logs/app.log
data/sample.csv
migrations/0001_init.sql
alembic/versions/0004_add_table.py
src/db/drop_table_helper.py
keys.py
monkey.py
assets/logo.svg
public/favicon.ico
LICENSE
CHANGELOG.md
examples/demo/run.py
app/models/token_usage.py
app/services/api_keys.py
docs/api-keys.md
.secrets.baseline
secrets.example.yml
src/secrets/__init__.py
tests/test_secrets.py
""".splitlines()
    if line
)

BENIGN_URLS = tuple(
    line
    for line in r"""
http://localhost:8000/v1/events
http://127.0.0.1:3000/
http://[::1]:8080/health
http://app.localhost/x
""".splitlines()
    if line
)

BENIGN_SQL = tuple(
    line
    for line in r"""
SELECT * FROM users WHERE id = 1
select count(*) from runs
INSERT INTO t (a) VALUES (1)
DELETE FROM sessions WHERE expires_at < now()
UPDATE users SET name = 'x' WHERE id = 3
EXPLAIN ANALYZE SELECT 1
BEGIN
COMMIT
SHOW TABLES
""".splitlines()
    if line
)

assert len(BENIGN_COMMANDS) >= 150
