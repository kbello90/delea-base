import { useEffect, useRef, useState } from "react";

/**
 * A search field for a list filter: reports the trimmed text 300 ms after typing stops.
 *
 * `refuse` (optional) vetoes a search (e.g. one that looks like an email address, which must
 * never reach a URL): the filter is cleared instead, the typed text stays in the box, and
 * `onRefused(true)` tells the page to explain why.
 */
export function SearchBox({
  id,
  placeholder,
  value,
  onChange,
  refuse,
  onRefused,
}: {
  id: string;
  placeholder: string;
  value: string;
  onChange: (q: string) => void;
  refuse?: (q: string) => boolean;
  onRefused?: (refused: boolean) => void;
}) {
  const [text, setText] = useState(value);
  // The latest callbacks, so a re-render (e.g. a refetch) doesn't restart the debounce.
  const latest = useRef({ onChange, refuse, onRefused });
  latest.current = { onChange, refuse, onRefused };
  // The last value this box reported. Only a change from elsewhere (Clear filters, the back
  // button) resets the text, so clearing a refused search doesn't wipe what was typed.
  const reported = useRef(value);
  useEffect(() => {
    if (value !== reported.current) {
      reported.current = value;
      setText(value);
    }
  }, [value]);
  useEffect(() => {
    const q = text.trim();
    const refused = Boolean(q && latest.current.refuse?.(q));
    const next = refused ? "" : q;
    if (next === value) {
      latest.current.onRefused?.(refused);
      return;
    }
    const timer = setTimeout(() => {
      reported.current = next;
      latest.current.onRefused?.(refused);
      latest.current.onChange(next);
    }, 300);
    return () => clearTimeout(timer);
  }, [text, value]);
  return (
    <div className="filter">
      <label htmlFor={id}>Search</label>
      <input id={id} type="search" placeholder={placeholder} value={text} onChange={(e) => setText(e.target.value)} />
    </div>
  );
}
