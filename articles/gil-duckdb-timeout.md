---
title: "GIL制約が刺さる場面: Celery + DuckDB + timeout-decoratorでプロセスリークを再現"
emoji: "🧵"
type: "tech"
topics: ["python", "gil", "celery", "duckdb", "docker"]
published: false
---

## まとめ

- GILの制約は「CPUバウンド処理をスレッドで並列化できない」だけではなく、
  **C拡張に対するタイムアウト/キャンセルが難しくなる** という形でも現れる。
- その穴埋めとしてプロセス分離（timeout-decoratorのmultiprocessing）が必要になると、
  **子プロセス/ゾンビが残り、リークのように見える現象** が起きやすい。
- 対策として **タイムアウト後に子プロセスを明示的に回収** すると、
  zombies を 0 に戻せることを検証した。
- free-threadedビルド（`--disable-gil`）でこの問題が消えるわけではないが、
  **「プロセス以外でタイムアウトを実装する」選択肢が現実的になる**。

この記事では、Docker Composeで **GILあり/なし** の環境を並べ、
`timeout-decorator` で DuckDB クエリをタイムアウトさせようとすると
プロセスリークのような状態になる現象を再現する。

## 背景: GIL制約の「具体的なデメリット」

GIL（Global Interpreter Lock）によって、CPythonでは
**同時に1つのスレッドしかPythonのバイトコードを実行できない**。
そのため、CPUバウンドな処理をスレッドで並列化しても速度が伸びない。

さらに実務で刺さるのが、**C拡張が絡む処理のタイムアウト/キャンセル**。
長時間走るC拡張（例: DuckDBやNumPyなど）を強制的に止めたいとき、
スレッドのままだとタイムアウトが効かず、
結局「別プロセスに隔離して殺す」設計になりやすい。

この回避策が、次のような副作用を生む:

- タスクごとにプロセスが増殖する
- タイムアウト発生時に後始末が追いつかず、
  **ゾンビプロセスが残る**
- Celeryのワーカープロセスにぶら下がる子プロセスが増え、
  「プロセスリーク」に見える

## 実験環境

- Python 3.14 をソースビルド
  - 通常ビルド（GILあり）
  - free-threadedビルド（`--disable-gil`）
- Celery + RabbitMQ
- DuckDB
- timeout-decorator

free-threadedビルドは `--disable-gil` で生成でき、
`PYTHON_GIL` 環境変数で実行時のGILを切り替えられる。
また `sys._is_gil_enabled()` で状態確認が可能。

## timeout-decoratorの仕様

`timeout-decorator` は通常 `SIGALRM` を使うが、
**シグナルが使えない場合は multiprocessing で別プロセス実行に切り替える**。
これは `use_signals=False` の挙動として明記されている。

Celeryのワーカーをスレッドプールで動かしているときや、
「C拡張が止められないのでプロセス分離が必須」なときに、
この経路に入りやすい。

ただし Celery のデフォルト `prefork` は **daemonプロセス** なので、
`use_signals=False` の multiprocessing で **`AssertionError: daemonic processes are not allowed to have children`** が出る。
本検証では `--pool=solo` に切り替えて回避した。
また Python 3.14 の `multiprocessing` は `forkserver` なので、
`timeout-decorator` に渡す関数は **トップレベル関数** にして `PicklingError` を避けた。

## 再現用 Docker Compose

本リポジトリに含めた構成は以下。

- `worker-gil`: GILありのPythonでCeleryワーカー
- `worker-nogil`: free-threadedビルド（GILなし）
- `producer`: タスク投入
- `worker-gil-clean`: タイムアウト後に子プロセスを回収する対策版

`tasks.leak_demo` というタスクが、
DuckDBクエリを `timeout-decorator` で制限時間付き実行する。
タイムアウトが多発する設定にすると、
**子プロセス/ゾンビが残る**のがログで確認できる。

### 実行手順

```bash
docker compose build

docker compose up -d rabbitmq worker-gil worker-gil-clean

docker compose run --rm producer

# それぞれのワーカーのログを見る
docker compose logs -f worker-gil
# 対策版
# docker compose logs -f worker-gil-clean
# GILなしも比較したい場合
# docker compose up -d worker-nogil
# docker compose logs -f worker-nogil
```

タイムアウトを確実に出したい場合は、例えば以下のように実行する。

```bash
docker compose run --rm -e TARGET_QUEUES=gil -e ITERATIONS=5 -e DUCKDB_ROWS=1000000000 -e DUCKDB_TIMEOUT=0.2 producer
```

ログ例（実測）:

```
iter=1 outcome=timeout children=3 zombies=1
iter=2 outcome=timeout children=3 zombies=1
iter=3 outcome=timeout children=3 zombies=1
```

`children=2` は `resource_tracker` と `forkserver` の常駐分なので、
`children=3` 以上になっているかがポイント。
`children` や `zombies` が残り続けるなら、
**プロセスリークに見える状態**が再現できている。

## 何が「GIL制約由来」なのか

ポイントは「**スレッドで止められないからプロセス分離に逃げる**」という構造。

- GILあり環境では、長時間C拡張が走ると
  **Python側からキャンセルできない**
- そこで `timeout-decorator` の multiprocessing fallback に頼る
- その結果、タスクごとに子プロセスが増え、
  **後処理が追いつかないとリーク的になる**

free-threadedビルドでは「スレッドで安全にキャンセルする」方に寄せられるが、
C拡張がfree-threaded対応していない場合はGILが再有効化されるので、
この問題が残るケースもある。

## 対策の検証: 子プロセスの明示的な回収

今回の再現では、`timeout-decorator` が multiprocessing fallback を使うため、
タイムアウト時に子プロセスが残りやすい。
そこで **タイムアウト後に子プロセスを terminate/kill して回収する** 対策を追加した。

### 実装

`CLEANUP_CHILDREN=1` を付けたワーカー (`worker-gil-clean`) では、
タイムアウト後に `psutil` で子プロセスを回収するようにしている。
`multiprocessing.resource_tracker` と `multiprocessing.forkserver` は
常駐プロセスなので対象から除外している。

```python
def _cleanup_children(timeout: float = 1.0) -> Dict[str, int]:
    proc = psutil.Process()
    children = proc.children(recursive=True)
    ...
```

### 実行手順

```bash
docker compose up -d rabbitmq worker-gil worker-gil-clean

# baseline (cleanupなし)
docker compose run --rm -e TARGET_QUEUES=gil producer
docker compose logs -f worker-gil

# cleanupあり
docker compose run --rm -e TARGET_QUEUES=gil-clean producer
docker compose logs -f worker-gil-clean
```

### 結果 (ログ例)

**baseline**

```
iter=1 outcome=timeout children=3 zombies=1
iter=2 outcome=timeout children=3 zombies=1
iter=3 outcome=timeout children=3 zombies=1
```

**cleanupあり**

```
iter=1 outcome=timeout children=2 zombies=0
cleanup terminated=1 killed=0 remaining=0
iter=2 outcome=timeout children=2 zombies=0
cleanup terminated=1 killed=0 remaining=0
```

`cleanup` を入れることで **zombies が 0 に戻る** ことが確認できた。
`children=2` は常駐プロセス分なので、余計な子プロセスが残っていない状態になる。

## 回避策・対策案

- timeout-decoratorのmultiprocessing fallbackを使う場合は、
  **子プロセスの回収 (terminate/kill + wait)** を必ず入れる
- Celeryの `soft_time_limit` / `time_limit` を活用する
- `--maxtasksperchild` でワーカープロセスを定期的に再起動
- DuckDBの `interrupt` を使い、専用スレッドで中断する
- CPUバウンド処理は別サービスへ切り出してプロセス管理を明示化

## 結論

GILのデメリットは「スレッドが遅い」だけではなく、
**タイムアウト/キャンセル設計が複雑になる**ことにある。
今回のように Celery + DuckDB で timeout-decorator を使うと、
GIL制約の回避策がプロセスリークの形で跳ね返ってくる。
ただし **タイムアウト後に子プロセスを回収** すれば、
ゾンビの残留は抑えられることも確認できた。

free-threadedビルドが普及すれば状況は改善する可能性があるが、
現状はC拡張の対応状況に左右される。

まずは「なぜプロセス分離が必要なのか」を理解し、
どこでリークが生まれるかを可視化することが重要だ。

## 参考

- https://docs.python.org/3/howto/free-threading-python.html
- https://docs.python.org/3/using/configure.html
- https://pypi.org/project/timeout-decorator/
- https://docs.celeryq.dev/en/stable/userguide/configuration.html#task-time-limit
