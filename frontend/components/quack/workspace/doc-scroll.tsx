import { cn } from "@/lib/utils";

/** The body of a document that is text: scrolls on its own, a comfortable measure inside */
export function DocScroll({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className={cn("mx-auto w-full max-w-[40rem] px-5 py-5", className)}>{children}</div>
    </div>
  );
}
