# BGM配置ディレクトリ

ここにBGMファイル(mp3/wav/m4a)を置くと、`render-video`実行時に
対話的に選択できるようになります(著作権の都合上、BGMファイル自体は
このリポジトリに同梱していません。各自で用意してください)。

```bash
cp あなたのBGM.mp3 video_pipeline/assets/bgm/
uv run render-video
```

`--bgm <パス>`で明示的に指定することもできます(この場合はこのディレクトリに
置く必要はありません)。
