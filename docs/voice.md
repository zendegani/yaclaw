# Voice Setup on Raspberry Pi 5 (ARM64)

This guide walks through configuring Speech-to-Text (STT) and Text-to-Speech (TTS) capabilities for Pishkar running on a Raspberry Pi 5.

## 1. Enable Voice & Set up STT (Groq Whisper)
Speech-to-Text is handled via the Groq API (which uses Whisper-large-v3). 

In your `.env` file, ensure the following are set:
```env
PISHKAR_VOICE_ENABLED=1
PISHKAR_STT_ENGINE=groq
GROQ_API_KEY=your_groq_api_key_here
```

## 2. Install Dependencies (FFmpeg)
The system requires `ffmpeg` to transcode Telegram's OGG/Opus audio files. Since the Pi 5 runs a Debian-based OS, install it via `apt`:

```bash
sudo apt update
sudo apt install ffmpeg
```

## 3. Install Piper TTS (for ARM64)
Since the Raspberry Pi 5 runs a 64-bit ARM OS, download the `aarch64` binary for Piper.

```bash
# Create the piper directory
mkdir -p ~/.pishkar/piper
cd ~/.pishkar/piper

# Download the aarch64 release of Piper
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz

# Extract it
tar -xf piper_linux_aarch64.tar.gz
```

## 4. Download a Voice Model
Download an `.onnx` voice model and its corresponding `.json` config file. The example below uses the `en_US-lessac-medium` voice.

```bash
cd ~/.pishkar/piper

# Download the model
curl -L -o en_US-lessac-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx

# Download the config
curl -L -o en_US-lessac-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
```

## 5. Configure `.env`
Update your `.env` file with the absolute paths to the Piper executable, the voice model, and the `ffmpeg` command. 
*(Assuming your Pi username is `pi5`)*:

```env
PISHKAR_TTS_ENGINE=piper
PISHKAR_PIPER_BIN=/home/pi5/.pishkar/piper/piper/piper
PISHKAR_PIPER_VOICE=/home/pi5/.pishkar/piper/en_US-lessac-medium.onnx
PISHKAR_FFMPEG_BIN=ffmpeg
```
