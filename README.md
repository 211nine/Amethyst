# Amethyst

Amethyst is a Windows Spotify companion with a customizable desktop overlay, playback controls, listening stats, profiles and leaderboards.

> Amethyst is an unofficial project and is not affiliated with or endorsed by Spotify or Last.fm.

## Features

- Customizable always-on-top Spotify overlay
- Play, pause, skip, previous, seek, shuffle, repeat and volume controls through Windows Media
- Global hotkey to show or hide the overlay
- Custom overlay size, position, opacity and colour
- Listening stats for songs, artists and albums
- Optional Last.fm connection for complete listening history
- Last.fm catch-up for music heard while Amethyst was closed or on other Spotify devices
- Profiles with avatars and public stat showcases
- Global song-play leaderboard
- Optional account security including TOTP 2FA

## How listening works

While Amethyst is open, it reads Spotify from Windows Media.

For the best experience, sign in to Amethyst and connect Last.fm. The connection happens on Last.fm's website, so Amethyst never sees your Last.fm password. Once Spotify Scrobbling is enabled in Last.fm, Amethyst can catch up listening from while the app was closed and from Spotify on your phone, console, web player and other connected devices.

If you do not connect Last.fm, Amethyst still works, but only music seen while Amethyst is open on this PC is added to your stats.

## Download

The easiest way to use Amethyst is to download the latest Windows build from the **Releases** section of this repository.

## Running from source

Amethyst is written in Python and uses PySide6.

1. Install Python.
2. Clone or download this repository.
3. Open a terminal in the project folder.
4. Install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

5. Start Amethyst:

```powershell
python main.py
```

## Development status

Amethyst is still in development, so bugs may happen. If you find one, feel free to open an issue.
