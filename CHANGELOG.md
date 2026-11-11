# Changelog

## 0.1.9

- Submit to the tested 晨羽智云 second-pass workflow with all six reference-image inputs.
- Preserve each enabled reference slot and upload the bundled default image once when any slots are empty.

## 0.1.8

- Make the first reference image optional on 晨羽智云 workflow revisions that expose its switch.
- Preserve the reference settings for previous revisions that expose switches for images 2–6.

## 0.1.7

- Synchronize the five optional reference-image switches on the corrected 晨羽智云 workflow.
- Check for missing reference images before uploading to legacy 晨羽智云 revisions.

## 0.1.6

- Add 晨羽智云 as a second cloud provider on the local sender with a configurable workflow revision.
- Upload handoff media as temporary assets, submit the published workflow, and save the second-pass video locally.

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
