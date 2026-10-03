"use client";

import {useId, useRef} from "react";

/**
 * A submit button that asks first, for actions that cannot be taken back.
 *
 * The native <dialog> traps focus, closes on Escape and returns focus to the
 * trigger, so the confirmation is keyboard-complete without a library. The
 * confirm button submits the form this sits in; cancelling submits nothing.
 */
export function ConfirmSubmit({
  label,
  title,
  body,
  confirmLabel,
  className,
  confirmClassName,
}: {
  label: string;
  title: string;
  body: string;
  confirmLabel: string;
  className: string;
  confirmClassName: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  return (
    <>
      <button type="button" className={className} onClick={() => dialog.current?.showModal()}>
        {label}
      </button>
      <dialog
        ref={dialog}
        role="alertdialog"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-body`}
        className="max-w-md rounded-2xl border border-rule bg-surface p-6 text-ink backdrop:bg-black/40"
      >
        <h2 id={`${id}-title`} className="font-display text-[17px] font-medium tracking-tight text-balance">
          {title}
        </h2>
        <p id={`${id}-body`} className="mt-2 text-[13px] leading-6 text-pretty text-ink-soft">
          {body}
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" className={className} onClick={() => dialog.current?.close()} autoFocus>
            Cancel
          </button>
          <button type="submit" className={confirmClassName} onClick={() => dialog.current?.close()}>
            {confirmLabel}
          </button>
        </div>
      </dialog>
    </>
  );
}
