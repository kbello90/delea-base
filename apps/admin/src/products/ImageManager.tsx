import { ApiError } from "@delea/api-client";
import { Button } from "@delea/ui";
import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useToast } from "../components/toastContext";
import { useImageActions, type ImageType, type Product, type ProductImage } from "./api";
import { uploadFile } from "./uploadFile";

const MAX_IMAGES = 10;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const TYPES: readonly ImageType[] = ["image/jpeg", "image/png", "image/webp"];

interface UploadItem {
  key: number;
  name: string;
  progress: number; // 0..1
  status: "uploading" | "processing" | "done" | "error";
  error?: string;
}

const message = (e: unknown, fallback: string) => (e instanceof ApiError || e instanceof Error ? e.message : fallback);

/**
 * Owner-only image manager. The server checks everything again (type by decoding the file,
 * size, pixel limit); the checks here only give quick feedback.
 */
export function ImageManager({ product }: { product: Product }) {
  const toast = useToast();
  const actions = useImageActions(product.id);
  const images = product.images;
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const [deleting, setDeleting] = useState<ProductImage | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);
  // Where focus goes after a move: [image id, which button].
  const pendingFocus = useRef<[string, "up" | "down"] | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());

  const inFlight = uploads.filter((u) => u.status === "uploading" || u.status === "processing").length;
  const room = MAX_IMAGES - images.length - inFlight;

  useEffect(() => {
    const target = pendingFocus.current;
    if (!target) return;
    const [id, direction] = target;
    const index = images.findIndex((image) => image.id === id);
    if (index === -1) return;
    // At an end the button in the moved direction is disabled; focus the other one.
    const atEdge = direction === "up" ? index === 0 : index === images.length - 1;
    const which = atEdge ? (direction === "up" ? "down" : "up") : direction;
    buttons.current.get(`${id}:${which}`)?.focus();
    pendingFocus.current = null;
  }, [images]);

  const patch = (key: number, change: Partial<UploadItem>) =>
    setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...change } : u)));

  async function uploadOne(file: File, key: number) {
    try {
      const type = file.type as ImageType;
      const ticket = await actions.signUpload(type);
      await uploadFile(ticket.upload_url, file, type, (fraction) => patch(key, { progress: fraction }));
      patch(key, { status: "processing", progress: 1 });
      await actions.register(ticket.upload_id, type);
      patch(key, { status: "done" });
    } catch (e) {
      patch(key, { status: "error", error: message(e, "The image couldn't be added.") });
    }
  }

  async function onFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // the same file can be picked again after an error
    let slots = room;
    const queue: [File, number][] = [];
    const items: UploadItem[] = files.map((file) => {
      const key = nextKey.current++;
      const base = { key, name: file.name, progress: 0 };
      if (!TYPES.includes(file.type as ImageType)) {
        return { ...base, status: "error" as const, error: "Only JPEG, PNG and WebP images can be added." };
      }
      if (file.size > MAX_FILE_BYTES) return { ...base, status: "error" as const, error: "The file is larger than 5 MB." };
      if (slots <= 0) return { ...base, status: "error" as const, error: `A product can have at most ${MAX_IMAGES} images.` };
      slots -= 1;
      queue.push([file, key]);
      return { ...base, status: "uploading" as const };
    });
    setUploads((list) => [...list.filter((u) => u.status !== "done"), ...items]);
    for (const [file, key] of queue) await uploadOne(file, key); // one at a time
  }

  function move(index: number, direction: "up" | "down") {
    const target = direction === "up" ? index - 1 : index + 1;
    const ids = images.map((image) => image.id);
    [ids[index], ids[target]] = [ids[target], ids[index]];
    const id = images[index].id;
    pendingFocus.current = [id, direction];
    actions.reorder.mutate(ids, {
      onSuccess: () => setAnnouncement(`Image moved to position ${target + 1} of ${ids.length}.`),
      onError: (e) => {
        pendingFocus.current = null;
        toast({ title: "Couldn't move the image", description: message(e, ""), variant: "error" });
      },
    });
  }

  const busy = actions.reorder.isPending || actions.setPrimary.isPending || actions.remove.isPending;

  return (
    <div className="image-manager">
      <p className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </p>

      {images.length === 0 ? (
        <p className="muted">No images yet. The first image you add becomes the primary image.</p>
      ) : (
        <ol className="image-list" aria-label="Images, in display order">
          {images.map((image, index) => {
            const label = `image ${index + 1}`;
            return (
              <li key={image.id} className="image-item">
                <img src={image.thumb_url} alt={`${product.name}, ${label} of ${images.length}`} width={96} height={96} />
                <div className="image-item__meta">
                  <span>
                    {index + 1} of {images.length}
                  </span>
                  {image.is_primary && <span className="tag tag--ok">Primary</span>}
                </div>
                <div className="image-item__actions">
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`Move ${label} up`}
                    disabled={index === 0 || busy}
                    ref={(el) => {
                      if (el) buttons.current.set(`${image.id}:up`, el);
                      else buttons.current.delete(`${image.id}:up`);
                    }}
                    onClick={() => move(index, "up")}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="icon-btn"
                    aria-label={`Move ${label} down`}
                    disabled={index === images.length - 1 || busy}
                    ref={(el) => {
                      if (el) buttons.current.set(`${image.id}:down`, el);
                      else buttons.current.delete(`${image.id}:down`);
                    }}
                    onClick={() => move(index, "down")}
                  >
                    ↓
                  </button>
                  {!image.is_primary && (
                    <button
                      type="button"
                      className="btn-text"
                      aria-label={`Make ${label} primary`}
                      disabled={busy}
                      onClick={() =>
                        actions.setPrimary.mutate(image.id, {
                          onSuccess: () => setAnnouncement(`Image ${index + 1} is now the primary image.`),
                          onError: (e) => toast({ title: "Couldn't change the primary image", description: message(e, ""), variant: "error" }),
                        })
                      }
                    >
                      Make primary
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-text btn-text--danger"
                    aria-label={`Delete ${label}`}
                    disabled={busy}
                    onClick={() => setDeleting(image)}
                  >
                    Delete
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      <div className="image-upload">
        <input
          ref={inputRef}
          id="image-files"
          type="file"
          accept={TYPES.join(",")}
          multiple
          className="visually-hidden"
          onChange={(e) => void onFiles(e)}
        />
        <Button type="button" variant="ghost" disabled={room <= 0} onClick={() => inputRef.current?.click()}>
          Add images
        </Button>
        <span className="muted">
          JPEG, PNG or WebP, up to 5 MB each. {Math.max(0, room)} of {MAX_IMAGES} slots left.
        </span>
      </div>

      {uploads.length > 0 && (
        <ul className="upload-list" aria-label="Uploads">
          {uploads.map((u) => (
            <li key={u.key} className={`upload upload--${u.status}`}>
              <span className="upload__name">{u.name}</span>
              {(u.status === "uploading" || u.status === "processing") && (
                <progress
                  max={100}
                  value={Math.round(u.progress * 100)}
                  aria-label={`Uploading ${u.name}`}
                />
              )}
              <span className="upload__status" role={u.status === "error" ? "alert" : undefined}>
                {u.status === "uploading" && `${Math.round(u.progress * 100)}%`}
                {u.status === "processing" && "Processing…"}
                {u.status === "done" && "Added"}
                {u.status === "error" && u.error}
              </span>
              {u.status === "error" && (
                <button
                  type="button"
                  className="btn-text"
                  aria-label={`Dismiss ${u.name}`}
                  onClick={() => setUploads((list) => list.filter((x) => x.key !== u.key))}
                >
                  Dismiss
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {deleting && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) {
              setDeleting(null);
              setDeleteError(null);
            }
          }}
          title="Delete this image?"
          description={<p>The image and its files will be deleted. This can't be undone.</p>}
          confirmLabel="Delete image"
          busyLabel="Deleting…"
          danger
          busy={actions.remove.isPending}
          error={deleteError}
          onConfirm={() =>
            actions.remove.mutate(deleting.id, {
              onSuccess: () => {
                setDeleting(null);
                setAnnouncement("Image deleted.");
              },
              onError: (e) => setDeleteError(message(e, "The image couldn't be deleted.")),
            })
          }
        />
      )}
    </div>
  );
}
