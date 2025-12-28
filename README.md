# zenn-about-gil

PythonのGIL制約と、Celery + DuckDB + timeout-decoratorでのタイムアウト処理の挙動を検証するための再現環境です。

## 構成

- Python 3.14 をソースからビルド
  - GILあり (通常ビルド)
  - GILなし (free-threadedビルド: `--disable-gil`)
- RabbitMQ + Celery (非同期タスク)
- DuckDBのクエリ実行
- `timeout-decorator` によるタイムアウト

## 使い方

### 1. イメージのビルド

```bash
docker compose build
```

### 2. ワーカーとRabbitMQを起動

```bash
docker compose up -d rabbitmq worker-gil worker-nogil
```

### 3. タスク投入

```bash
docker compose run --rm producer
```

### 4. ログ確認

```bash
docker compose logs -f worker-gil
# または
# docker compose logs -f worker-nogil
```

`iter=... outcome=timeout children=... zombies=...` のようなログが出るので、
タイムアウト時に子プロセスが増え続けるかどうかを確認します。

## 重要な注意点

- free-threadedビルドでも、C拡張が対応していない場合にGILが再有効化されることがあります。
  ログの `gil_status` に `gil_enabled` が出るので、まずそこを確認してください。
- `duckdb` が free-threaded ビルド向けのホイールを持っていない場合、ソースビルドになります。
  失敗する場合は `duckdb` のバージョンを上げるか、`PYTHON_VERSION` を下げてください。
- `TIMEOUT_USE_SIGNALS=1` の場合、C拡張が長時間走るとタイムアウトが効かないことがあります。
  その場合は `TIMEOUT_USE_SIGNALS=0` に切り替えて再実行してください。
- `timeout-decorator` の multiprocessing fallback は **daemonプロセスから子プロセスを作れない** ため、
  Celeryのデフォルト `prefork` では失敗します。`compose.yml` では `--pool=solo` を使っています。
- `DUCKDB_ROWS` と `DUCKDB_TIMEOUT` は環境によって調整が必要です。

## パラメータ調整

`compose.yml` か `docker compose run` の環境変数で変更します。

- `DUCKDB_ROWS`: クエリ対象の行数
- `DUCKDB_TIMEOUT`: タイムアウト秒数
- `ITERATIONS`: タスク内で繰り返す回数
- `TARGET_QUEUES`: `producer` がタスクを送るキューの一覧 (例: `gil,gil-clean`)

## 対策検証 (子プロセスの掃除)

`timeout-decorator` の multiprocessing fallback で子プロセスが残る場合に、
**明示的に子プロセスを terminate/kill する** 対策を入れたワーカーが `worker-gil-clean` です。

```bash
docker compose up -d rabbitmq worker-gil-clean
docker compose run --rm -e TARGET_QUEUES=gil-clean -e DUCKDB_ROWS=1000000000 -e DUCKDB_TIMEOUT=0.2 producer
docker compose logs -f worker-gil-clean
```

`cleanup terminated=... killed=... remaining=...` のログが出るので、
`children` / `zombies` の増加が抑えられるかを確認します。

例:

```bash
docker compose run --rm -e DUCKDB_ROWS=1000000000 -e DUCKDB_TIMEOUT=1 producer
```
