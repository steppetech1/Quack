import { cn } from "@/lib/utils";

type Props = React.ComponentProps<"button"> & { label: string };

/** Кнопка-иконка 40×40 с обязательной подписью для скринридера: круглая, без заливки в покое */
export function IconButton({ label, className, children, ...rest }: Props) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex size-10 shrink-0 items-center justify-center rounded-pill text-paper-dim transition-colors duration-[var(--dur-s)] ease-out hover:bg-cloth-hover hover:text-paper [&_svg]:size-[18px] [&_svg]:stroke-[1.6]",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
