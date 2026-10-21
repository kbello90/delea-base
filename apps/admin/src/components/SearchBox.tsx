import { useEffect, useRef, useState } from "react";

/** A search field for a list filter: reports the trimmed text 300 ms after typing stops. */
export function SearchBox({
  id,
  placeholder,
  value,
  onChange,
}: {
  id: string;
  placeholder: string;
  value: string;
  onChange: (q: string) => void;
}) {
  const [text, setText] = useState(value);
  // The latest callback, so a re-render (e.g. a refetch) doesn't restart the debounce.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (text.trim() === value) return;
    const timer = setTimeout(() => onChangeRef.current(text.trim()), 300);
    return () => clearTimeout(timer);
  }, [text, value]);
  return (
    <div className="filter">
      <label htmlFor={id}>Search</label>
      <input id={id} type="search" placeholder={placeholder} value={text} onChange={(e) => setText(e.target.value)} />
    </div>
  );
}
