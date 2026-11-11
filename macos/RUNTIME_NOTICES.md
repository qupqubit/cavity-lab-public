# CavityFEM v0.1.8：macOS 同梱 Python とライセンス

CavityFEM の macOS アプリは、Astral の python-build-standalone **20261110** リリースから、未改変の **CPython 3.12.16 install_only** 配布物を使用します。Apple Silicon と Intel の Python を別々に保持し、実行する CPU に対応するものを選びます。この文書と `runtime-licenses/` をアプリの配布物にも同梱してください。

Python 実行環境だけを同梱します。NGSolve、Netgen、NumPy、SciPy などの計算用パッケージと、ユーザーの CAD・計算結果は含めません。計算用パッケージは初回起動時に PyPI から、その Mac の Application Support 内の専用環境へインストールします。それらのライセンスは [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) と各インストール済みパッケージの通知に従います。

v0.1.8では表示名と配布名を `CavityFEM.app` / `CavityFEM-macOS.zip` に変更しています。ブラウザ版の配布名は `cavity-lab-public.zip` のままです。既存のデータと設定を引き継ぐため、bundle identifier `jp.qupqubit.cavitylab` と保存先 `~/Library/Application Support/Cavity Lab/data` は維持します。Pythonランタイムの版は変更していません。

アプリアイコンは提供された画像とその形式変換版を使用します。これはPythonの一部でもMITの自作サンプルでもありません。[アイコンの通知](icon-NOTICE.md) を参照してください。

## 配布物の特定

| CPU | 公式配布物 | SHA-256 |
| --- | --- | --- |
| Apple Silicon | [aarch64-apple-darwin install_only](https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%2B20261110-aarch64-apple-darwin-install_only.tar.gz) | `e956e60bc9465d577ef8062059aa05cd3a2665b4b8678a2ed24e432368330b8f` |
| Intel | [x86_64-apple-darwin install_only](https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%2B20261110-x86_64-apple-darwin-install_only.tar.gz) | `83f2922437e6d546d0fcdb8c11f150a8e52787e8d9ea8651b1974e20908eb1bc` |

公開された GitHub release asset の SHA-256 とダウンロードしたファイルの値を照合しました。展開時には絶対パス、親ディレクトリへの移動、外部を指すリンク、リンク経由の書き込み、特殊ファイルを拒否しました。上記は署名や Apple の公証を確認したという意味ではありません。

[リリース](https://github.com/astral-sh/python-build-standalone/releases/tag/20261110) と、各コンポーネントの元ソース・版・ハッシュを記録した [PROVENANCE.json](runtime-licenses/PROVENANCE.json) から再取得できます。同じリリースの full archive にある `PYTHON.json` と `licenses/` を照合し、install_only に含まれない通知を補っています。full archive のビルド中間生成物はアプリに含めません。

## 著作権・ライセンスの保持

Python 全体を CavityFEM 自体のライセンスへ変更するものではありません。各部品の条件は同梱した原文を参照してください。

| 部品 | このランタイムの通知 |
| --- | --- |
| CPython 3.12.16 | [Python/CNRI 等の原文](runtime-licenses/LICENSE.cpython.txt)、[CPython に含まれる第三者通知](runtime-licenses/CPython-third-party-license.rst) |
| OpenSSL 3.5.9 | [Apache-2.0](runtime-licenses/LICENSE.openssl-3.5.9.txt) |
| bzip2 1.0.8 | [bzip2](runtime-licenses/LICENSE.bzip2.txt) |
| Expat 2.10.0、libffi 3.8.0 | [Expat MIT](runtime-licenses/LICENSE.expat.txt)、[libffi MIT](runtime-licenses/LICENSE.libffi.txt) |
| libedit 20260512-3.1、libuuid | [libedit BSD](runtime-licenses/LICENSE.libedit.txt)、[libuuid BSD](runtime-licenses/LICENSE.libuuid.txt) |
| libmpdec 4.0.0、ncurses 6.5 | [BSD-2-Clause](runtime-licenses/LICENSE.mpdecimal.txt)、[X11](runtime-licenses/LICENSE.ncurses.txt) |
| liblzma 5.8.4 | [0BSD](runtime-licenses/LICENSE.liblzma.txt) |
| SQLite 3.53.1 | [Public domain notice](runtime-licenses/LICENSE.sqlite.txt) |
| zlib / zlib-ng | [zlib](runtime-licenses/LICENSE.zlib.txt)、[zlib-ng](runtime-licenses/LICENSE.zlib-ng.txt) |
| Tcl/Tk 9.0.4、Itcl 4.3.8、Thread 3.0.6 | [Tcl](runtime-licenses/LICENSE.tcl-9.0.4.txt)、[Tk](runtime-licenses/LICENSE.tk-9.0.4.txt)、[Itcl](runtime-licenses/LICENSE.itcl-4.3.8.txt)、[Thread](runtime-licenses/LICENSE.tcl-thread-3.0.6.txt)、[LibTomMath](runtime-licenses/LICENSE.libtommath.txt) |
| pip 26.3 と同梱ライブラリ | [pip のライセンスと著者](runtime-licenses/pip/LICENSE.txt)、[同梱版一覧](runtime-licenses/pip/vendor.txt)、同ディレクトリ内の各部品の原文 |

これらの原文に加え、ランタイム内の `lib/python3.12/LICENSE.txt`、pip の `dist-info/licenses/`、各ソースに付された著作権表示を保持します。上流ソースへの変更はありません。署名・パッケージ化などの配布作業を行う場合にも、通知を削除しません。

python-build-standalone のビルドシステム自体は [MPL-2.0](runtime-licenses/LICENSE.python-build-standalone.txt) です。これは CPython やすべての依存ライブラリを一括で MPL にするものではありません。ビルド用ソースとパッチは [固定リリースのソース](https://github.com/astral-sh/python-build-standalone/tree/20261110) から取得できます。

pip が含む certifi 2026.10.23 は MPL-2.0 の対象です。その通知を保持し、ランタイムの `pip/_vendor/certifi/` と [certifi-source/](runtime-licenses/certifi-source/) に、実際に同梱した編集可能な Python ソースと可読の証明書バンドルを配置しています。元のソース配布物の URL とハッシュも PROVENANCE.json に記録しています。この部分には MPL-2.0 が適用され、受領者の同ライセンス上の権利を制限しません。対象ソースを変更して再配布する場合には、変更後の対象ソースも同じ条件で提供する必要があります。[MPL の配布条件](https://www.mozilla.org/en-US/MPL/2.0/#distribution-of-source-form)

今回確認した Mac 向け `PYTHON.json` では、readline 拡張は BSD 系の libedit を使い、GNU readline/gdbm をリンクする構成ではありません。OpenSSL は Apache-2.0、liblzma は 0BSD です。NGSolve 等の LGPL 部品をアプリへ追加同梱する変更は、このランタイム調査の範囲に含みません。その場合は対応するソース提供・再リンク等の条件を別途確認してください。

## 移動と実行の確認

上流はインストール先を固定しない自己完結したランタイムとして配布しています。[配布形式](https://docs.astral.sh/python-build-standalone/distributions/)

v0.1.7のランタイム確認では、Apple Silicon で展開後さらに空白を含む別ディレクトリへコピーして、Python 3.12.16、ssl、sqlite3、ctypes、zlib、bz2、lzma、venv、ensurepip の動作を確認しました。その Python から専用 venv を作り、venv 内の pip 26.3 も起動しました。Intel 版は Mach-O x86_64 と配布メタデータを確認しており、Intel 実機での起動試験は行っていません。

Python の配布メタデータ上の最低 OS は ARM が macOS 11.0、Intel が 10.15 です。これは Python 単体の条件であり、アプリ本体や計算用パッケージを含む全体の対応 OS を保証する値ではありません。

NGSolve 6.2.2506 と netgen-mesher 6.2.2506 には、Python 3.12 の `macosx_10_15_universal2` wheel が PyPI にあります。両 CPU 向け配布物の存在を確認したもので、Intel の計算試験の代わりにはなりません。[NGSolve の配布記録](https://pypi.org/pypi/ngsolve/6.2.2506/json)、[Netgen の配布記録](https://pypi.org/pypi/netgen-mesher/6.2.2506/json)

起動時の導入処理は `python -m pip` と配布済み wheel を使います。生のランタイムの sysconfig にはビルド環境に由来する値もあるため、そのまま利用者の Mac 上でソースから拡張をコンパイルすることは前提にしません。アプリの署名後にその内部へ追加インストールせず、専用環境を Application Support 側に保持します。

## 同じ入力からのアプリ作成

公開ソースには [runtime-manifest.json](runtime-manifest.json) を同梱しています。そこに記載された二つの公式アーカイブを取得し、SHA-256 を照合し、別々のディレクトリへ安全に展開します。既存の仮想環境を代用しません。Mac 上で Xcode Command Line Tools を用意し、リポジトリ直下から次の形で [build_macos_app.py](../scripts/build_macos_app.py) を実行できます。`<...>` は展開先などに置き換えてください。

```sh
python3 scripts/build_macos_app.py \
  --python-arm64 '<ARM 展開先>/python' \
  --python-x86_64 '<Intel 展開先>/python' \
  --runtime-manifest macos/runtime-manifest.json \
  --runtime-notices macos/runtime-licenses \
  --output '<出力先>/CavityFEM.app' \
  --archive '<出力先>/CavityFEM-macOS.zip'
```

この処理はソースから Swift の両 CPU 向けランチャーを作り、指定した Python と通知を配置して、ローカル実行用の ad-hoc 署名を行います。FEM のダウンロードやインストールは行いません。ZIP の名前は `CavityFEM-macOS.zip` です。ad-hoc 署名は Apple Developer ID 署名や公証ではありません。外部配布の公証を実施したという意味にはなりません。
