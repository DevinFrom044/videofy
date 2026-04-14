# Videofy

Videofy is a local Mac app for generating preview videos from prepared templates.

## The easiest way to start

1. Open the `Videofy` folder.
2. Double-click `run.command`.
3. If macOS shows a warning, allow the file in `System Settings -> Privacy & Security`, then open it again.
4. Wait a few seconds. The app will open in your browser at:

```text
http://127.0.0.1:8765
```

Important:

- Keep the Terminal window open while you use the app.
- To stop the app, return to Terminal and press `Control + C`.

## What must already be installed on the Mac

- Python 3
- Node.js
- ffmpeg
- Google Chrome

## If double-click does not work

1. Open `Terminal`
2. Go to the project folder:

```bash
cd /path/to/Videofy
```

3. Start the app manually:

```bash
bash run.command
```

## What the app can do

- `AI Photo`
- `AI Filter`
- `AI Video`
- `2 Photos`

## Templates included in this package

- `templates_json/AI-PHOTO.json`
- `templates_json/maska-worksGood.json`
- `templates_json/AI-VIDEO (1).json`
- `templates_json/2-Photo-Flow (1).json`

## First launch

If project dependencies are missing, `run.command` will try to install them automatically:

- Python packages from `requirements.txt`
- Node packages from `package.json`

This may take a minute on the first run.

## Deploy to Railway

This project is prepared for Railway using the included `Dockerfile`.

### Step by step

1. Push this project to GitHub.
2. Go to Railway and sign in with GitHub.
3. Click `New Project`.
4. Choose `Deploy from GitHub repo`.
5. Select your `videofy` repository.
6. Railway will detect the `Dockerfile` and build the app automatically.
7. After the deploy finishes, open the service in Railway.
8. Go to `Settings` or `Networking` and generate a public domain.
9. Open the generated Railway URL.

### Notes

- The app listens on `0.0.0.0:$PORT` inside Railway.
- Chromium and `ffmpeg` are installed in the container.
- Generated output files are stored inside the Railway service filesystem.
- For long-term production usage, external object storage is recommended.
