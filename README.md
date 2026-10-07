# Word-salad

Word-salad is a collection of wildcards for AI image prompts: activities, characters, clothing, colors, lighting, styles, and more. Wildcards let your image tool pick from a list, giving you different results from the same prompt.

## Use it

Requires Python 3.10 or newer.

1. Download or clone this repository and open a terminal in its folder.
2. Generate the wildcard files:

   ```bash
   python3 main.py sync
   ```

3. Copy the contents of the generated `../Wildcards/` folder into your image tool's wildcard folder, keeping the subfolders.

In SwarmUI, use a wildcard in your prompt like this:

```text
a person <wc:activities/outdoor>, <wc:lighting>
```

Each wildcard picks an entry from the matching file. For example, `<wc:activities/outdoor>` can become `hiking along a wooded trail` or `painting a watercolor landscape beside a pond`.

If the first run asks you to adopt an existing Wildcards folder, run `python3 main.py adopt` once.

## Make it yours

Edit or add `.txt` files in `_data/`, with one choice per line. Run `python3 main.py sync` again to update the generated files.

To update them automatically while you edit:

```bash
python3 main.py watch
```

Press Ctrl+C to stop. If you copied the wildcards into another app's folder, copy the updated files there too.
