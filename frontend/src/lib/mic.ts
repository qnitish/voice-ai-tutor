/** Triggers the browser's mic permission prompt and releases the mic straight away; the lecture plays without it. */
export async function askForMic(onDenied: () => void) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.getTracks().forEach((t) => t.stop())
  } catch {
    onDenied()
  }
}
