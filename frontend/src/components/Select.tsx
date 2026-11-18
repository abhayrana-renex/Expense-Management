import {
  Children, isValidElement, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type ChangeEvent, type CSSProperties, type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Search } from "lucide-react";

/** Above this many options the dropdown gets a search box. */
export const SEARCH_ABOVE = 10;

interface Opt { value: string; label: string; disabled: boolean }

function text(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return text(node.props.children);
  return "";
}

/** The <option>s inside the children, through arrays, fragments and conditionals. */
function optionsOf(children: ReactNode): Opt[] {
  const out: Opt[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement<{ value?: string | number; children?: ReactNode; disabled?: boolean }>(child)) return;
    if (child.type === "option") {
      const label = text(child.props.children);
      out.push({ value: child.props.value === undefined ? label : String(child.props.value),
        label, disabled: !!child.props.disabled });
    } else {
      out.push(...optionsOf(child.props.children));
    }
  });
  return out;
}

type Props = {
  value: string | number;
  onChange?: (e: ChangeEvent<HTMLSelectElement>) => void;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  disabled?: boolean;
  "aria-label"?: string;
  title?: string;
};

/** Drop-in for <select>: the native one for short lists, a searchable list above
 *  {@link SEARCH_ABOVE} options. onChange gets an event-like object (e.target.value). */
export function Select(props: Props) {
  const options = useMemo(() => optionsOf(props.children), [props.children]);
  if (options.length <= SEARCH_ABOVE) {
    const { children, className = "select", ...rest } = props;
    return <select className={className} {...rest}
      value={rest.value} onChange={rest.onChange}>{children}</select>;
  }
  return <SearchSelect {...props} options={options} />;
}

function SearchSelect({ value, onChange, options, className = "select", style, disabled, title,
  "aria-label": ariaLabel }: Props & { options: Opt[] }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<CSSProperties>({});
  const button = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const current = options.find((o) => o.value === String(value));

  const shown = useMemo(() => {
    const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return words.length ? options.filter((o) => words.every((w) => o.label.toLowerCase().includes(w)))
      : options;
  }, [q, options]);

  function place() {
    const r = button.current?.getBoundingClientRect();
    if (!r) return;
    const below = window.innerHeight - r.bottom;
    const height = Math.min(320, Math.max(below, r.top) - 12);
    const up = below < 220 && r.top > below;
    setPos({ left: r.left, width: Math.max(r.width, 220), maxHeight: height,
      ...(up ? { bottom: window.innerHeight - r.top + 4 } : { top: r.bottom + 4 }) });
  }

  function show() {
    if (disabled) return;
    place();
    setQ("");
    setActive(Math.max(0, options.findIndex((o) => o.value === String(value))));
    setOpen(true);
  }

  function close(focus = true) {
    setOpen(false);
    if (focus) button.current?.focus();
  }

  function pick(o: Opt) {
    if (o.disabled) return;
    if (o.value !== String(value)) {
      onChange?.({ target: { value: o.value }, currentTarget: { value: o.value } } as unknown as
        ChangeEvent<HTMLSelectElement>);
    }
    close();
  }

  useLayoutEffect(() => { if (open) place(); }, [open]);
  useEffect(() => { setActive(0); }, [q]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!pop.current?.contains(t) && !button.current?.contains(t)) close(false);
    };
    const moved = (e: Event) => { if (!pop.current?.contains(e.target as Node)) close(false); };
    // Escape closes only this list, not the dialog or drawer underneath (they listen on window).
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    };
    const el = pop.current;
    el?.addEventListener("keydown", esc);
    document.addEventListener("mousedown", outside);
    window.addEventListener("resize", moved);
    window.addEventListener("scroll", moved, true);
    return () => {
      el?.removeEventListener("keydown", esc);
      document.removeEventListener("mousedown", outside);
      window.removeEventListener("resize", moved);
      window.removeEventListener("scroll", moved, true);
    };
  }, [open]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  function keys(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(shown.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); const o = shown[active]; if (o) pick(o); }
    else if (e.key === "Tab") close(false);
  }

  return (
    <>
      <button ref={button} type="button" className={`${className} ss-button`} style={style}
        disabled={disabled} title={title ?? current?.label} aria-label={ariaLabel}
        aria-haspopup="listbox" aria-expanded={open}
        onClick={() => (open ? close() : show())}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
            e.preventDefault(); show();
          } else if (!open && e.key.length === 1 && /\S/.test(e.key)) {
            show(); setQ(e.key); e.preventDefault();
          }
        }}>
        <span className={`ss-label ${current && current.value !== "" ? "" : "muted"}`}>
          {current?.label ?? ""}</span>
        <ChevronDown size={14} className="ss-chevron" />
      </button>
      {open && createPortal(
        <div ref={pop} className="ss-pop" style={pos} onKeyDown={keys}>
          <div className="ss-search"><Search size={14} />
            <input autoFocus value={q} placeholder={`Search ${options.length} options…`}
              aria-label="Search options" onChange={(e) => setQ(e.target.value)} /></div>
          <ul ref={list} role="listbox" className="ss-list">
            {shown.length === 0 && <li className="ss-empty">No match for “{q}”</li>}
            {shown.map((o, i) => (
              <li key={`${o.value}-${i}`} data-i={i} role="option"
                aria-selected={o.value === String(value)} aria-disabled={o.disabled}
                className={`ss-opt ${i === active ? "active" : ""} ${o.value === "" ? "muted" : ""}`}
                onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(o)}>
                <span>{o.label}</span>
                {o.value === String(value) && <Check size={14} />}
              </li>))}
          </ul>
        </div>, document.body)}
    </>
  );
}
