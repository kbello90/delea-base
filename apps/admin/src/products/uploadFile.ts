/**
 * PUTs a file to a signed Storage upload URL with progress (fetch can't report upload
 * progress, so this uses XMLHttpRequest). Resolves on 2xx; rejects with a readable message.
 * The signed URL is the only credential: no app token is sent to Storage.
 */
export function uploadFile(
  url: string,
  file: Blob,
  contentType: string,
  onProgress: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && event.total > 0) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(1);
        resolve();
      } else if (xhr.status === 413) {
        reject(new Error("The file is larger than 5 MB."));
      } else {
        reject(new Error(`The upload failed (${xhr.status}). Try again.`));
      }
    };
    xhr.onerror = () => reject(new Error("The upload failed: can't reach file storage. Try again."));
    xhr.send(file);
  });
}
