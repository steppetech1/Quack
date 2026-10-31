/** Текст, в котором числа набраны моноширинным: «26 октября», «через 3 дня» */
export function NumText({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\d+)/).map((p, i) => (i % 2 ? <span key={i} className="num">{p}</span> : p))}
    </>
  );
}
