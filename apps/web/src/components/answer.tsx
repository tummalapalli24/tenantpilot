import { Badge } from "@/components/ui";
import type { FinalAnswer } from "@/lib/types";

export function SourceChip({ id }: { id: string }) {
  const tool = id.startsWith("T");
  return (
    <span
      title={tool ? "Tool result" : "Knowledge excerpt"}
      className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold ${tool ? "bg-sky-100 text-sky-800" : "bg-violet-100 text-violet-800"}`}
    >
      {id}
    </span>
  );
}

export function AnswerView({ answer }: { answer: FinalAnswer }) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-slate-500">confidence</span>
        <Badge value={answer.confidence} />
        {answer.needs_human_followup && <Badge value="needs follow-up" className="bg-amber-50 text-amber-800 ring-amber-200" />}
      </div>
      <p className="text-sm leading-relaxed text-slate-800">{answer.summary}</p>
      {answer.findings.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Findings (with evidence)</h3>
          <ul className="space-y-2">
            {answer.findings.map((f, i) => (
              <li key={i} className="flex gap-2 text-sm text-slate-700">
                <span className="flex shrink-0 gap-1 pt-0.5">
                  {f.source_ids.map((id) => (
                    <SourceChip key={id} id={id} />
                  ))}
                </span>
                <span>{f.statement}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {answer.recommended_actions.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Recommended actions</h3>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-700">
            {answer.recommended_actions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
