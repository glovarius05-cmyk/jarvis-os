import { Fragment, useState } from "react";
import { Check, Copy, Download } from "lucide-react";

function renderInline(line: string) {
  const parts = line.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={idx}
          className="rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[0.85em] text-amber-300"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return (
        <strong key={idx} className="font-semibold text-zinc-50">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <Fragment key={idx}>{part}</Fragment>;
  });
}

function CodeBlock({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    void navigator.clipboard?.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  const handleDownload = () => {
    const raw = (lang || "").toLowerCase().trim();
    const extMap: Record<string, string> = {
      python: "py",
      py: "py",
      javascript: "js",
      js: "js",
      typescript: "ts",
      ts: "ts",
      json: "json",
      markdown: "md",
      md: "md",
      shell: "sh",
      bash: "sh",
      sh: "sh",
      zsh: "sh",
      html: "html",
      css: "css",
      sql: "sql",
      yaml: "yaml",
      yml: "yaml",
      text: "txt",
      txt: "txt",
    };
    const ext = extMap[raw] || (raw.length <= 4 && /^[a-z0-9]+$/.test(raw) ? raw : "txt");
    const filename = `jarvis_file_${Date.now()}.${ext}`;
    const blob = new Blob([code], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950">
      <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-1 text-xs text-zinc-400">
        <span className="font-mono">{lang || "code"}</span>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleDownload}
            className="flex items-center gap-1 text-zinc-400 hover:text-sky-300 transition"
            title="Скачать файл на компьютер"
          >
            <Download className="h-3.5 w-3.5" />
            <span>Скачать</span>
          </button>
          <button
            type="button"
            onClick={handleCopy}
            className="flex items-center gap-1 text-zinc-400 hover:text-zinc-200 transition"
          >
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
            <span>{copied ? "Copied" : "Copy"}</span>
          </button>
        </div>
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed text-zinc-200">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export function MarkdownText({ content }: { content: string }) {
  const segments = content.split(/(```[\s\S]*?```)/g);

  return (
    <div className="space-y-2 text-sm leading-relaxed text-zinc-200">
      {segments.map((seg, i) => {
        if (seg.startsWith("```") && seg.endsWith("```")) {
          const inner = seg.slice(3, -3);
          const firstNewline = inner.indexOf("\n");
          const lang = firstNewline >= 0 ? inner.slice(0, firstNewline).trim() : "";
          const code = firstNewline >= 0 ? inner.slice(firstNewline + 1) : inner;
          return <CodeBlock key={i} lang={lang} code={code} />;
        }

        const lines = seg.split("\n");
        return (
          <div key={i} className="space-y-1.5">
            {lines.map((line, j) => {
              if (!line.trim()) return <div key={j} className="h-1" />;
              if (line.startsWith("### ")) {
                return (
                  <h4 key={j} className="pt-1 text-sm font-semibold text-zinc-50">
                    {renderInline(line.slice(4))}
                  </h4>
                );
              }
              if (line.startsWith("## ")) {
                return (
                  <h3 key={j} className="pt-1 text-base font-semibold text-zinc-50">
                    {renderInline(line.slice(3))}
                  </h3>
                );
              }
              if (line.startsWith("- ") || line.startsWith("* ")) {
                return (
                  <div key={j} className="flex gap-2 pl-2">
                    <span className="text-amber-400">•</span>
                    <span className="flex-1">{renderInline(line.slice(2))}</span>
                  </div>
                );
              }
              return <p key={j}>{renderInline(line)}</p>;
            })}
          </div>
        );
      })}
    </div>
  );
}
