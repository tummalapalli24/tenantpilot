import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-300",
  secondary: "bg-white text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50 disabled:text-slate-400",
  danger: "bg-rose-600 text-white hover:bg-rose-500 disabled:bg-rose-300",
  ghost: "text-slate-600 hover:bg-slate-100",
};

export function Button({ variant = "primary", className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      {...props}
      className={cx("inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed", VARIANTS[variant], className)}
    />
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-lg border border-slate-200 bg-white shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          <div className="flex gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const BADGE: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  succeeded: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  indexed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  healthy: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  active: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  approved: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  high: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  awaiting_approval: "bg-amber-50 text-amber-800 ring-amber-200",
  pending_approval: "bg-amber-50 text-amber-800 ring-amber-200",
  pending: "bg-amber-50 text-amber-800 ring-amber-200",
  queued: "bg-amber-50 text-amber-800 ring-amber-200",
  processing: "bg-amber-50 text-amber-800 ring-amber-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  medium: "bg-amber-50 text-amber-800 ring-amber-200",
  blocked: "bg-slate-100 text-slate-700 ring-slate-300",
  denied: "bg-slate-100 text-slate-700 ring-slate-300",
  unknown: "bg-slate-100 text-slate-700 ring-slate-300",
  failed: "bg-rose-50 text-rose-700 ring-rose-200",
  error: "bg-rose-50 text-rose-700 ring-rose-200",
  rejected: "bg-rose-50 text-rose-700 ring-rose-200",
  unhealthy: "bg-rose-50 text-rose-700 ring-rose-200",
  suspended: "bg-rose-50 text-rose-700 ring-rose-200",
  low: "bg-rose-50 text-rose-700 ring-rose-200",
};

export function Badge({ value, className }: { value: string; className?: string }) {
  return (
    <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", BADGE[value] ?? "bg-indigo-50 text-indigo-700 ring-indigo-200", className)}>
      {value.replaceAll("_", " ")}
    </span>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

const inputClass = "w-full rounded-md border-0 px-2.5 py-1.5 text-sm text-slate-900 ring-1 ring-inset ring-slate-300 placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-500 disabled:bg-slate-50";

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(inputClass, props.className)} />;
}

export function Textarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(inputClass, "font-sans", props.className)} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputClass, "bg-white", props.className)} />;
}

export function ErrorBanner({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  const requestId = (error as { requestId?: string | null }).requestId;
  return (
    <div className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700 ring-1 ring-rose-200">
      {message}
      {requestId && <span className="ml-2 font-mono text-xs text-rose-500">request {requestId}</span>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-600" />
      {label}
    </div>
  );
}

export function JsonView({ value, maxHeight = "20rem" }: { value: unknown; maxHeight?: string }) {
  return (
    <pre className="overflow-auto rounded-md bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-100" style={{ maxHeight }}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-semibold text-slate-900">{value}</div>
      {sub && <div className="text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "-";
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
