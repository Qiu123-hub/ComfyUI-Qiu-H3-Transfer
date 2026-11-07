# Changelog

## 0.1.5

- Submit and save RunningHub second-pass results from the local sender using the paired API workflow template.
- Choose 1.0 to 2.0 megapixels in steps of 0.1 on the sender node. The default is 1.5.
- Include the paired API workflow profile with cleared media selections and prompt.

## 0.1.4

- Import H3 packages on RunningHub through its standard media upload route when the receiver is used to fill the canvas.
- Check package hashes and all target widgets before uploading and apply the settings after the required media are available.

## 0.1.3

- Raise the clipboard package limit to 128 MiB with block-based Base64 for older browsers.
- Register each ZIP as a task artifact and recover downloads through the standard output route or the task's managed storage link.
- Show upload progress and distinguish unavailable import routes from platform size limits.

## 0.1.2

- One local first-pass video for preview, continuation, and handoff.
- Tail padding runs in the cloud workflow before second-pass VAE encoding.
- Include example workflows with media and personal prompts cleared.

## 0.1.1

- Explicitly connect a 24fps Float to VHS output frame_rate inputs.

## 0.1.0

- Sender and receiver nodes with clipboard and ZIP import/export.
- Preserve reference media, prompt, common settings, and image slot switches.
- Validate archive contents and media hashes; preserve second-pass sampling settings.
