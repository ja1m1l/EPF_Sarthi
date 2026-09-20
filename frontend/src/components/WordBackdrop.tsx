const WORDS = [
  { text: 'भविष्य निधि', top: '22%', left: '3%', size: 'text-3xl md:text-5xl', rotate: '-12deg' },
  { text: 'Provident Fund', top: '16%', right: '3%', size: 'text-2xl md:text-4xl', rotate: '8deg' },
  { text: 'दावा', top: '58%', left: '5%', size: 'text-4xl md:text-6xl', rotate: '10deg' },
  { text: 'Form 19', top: '44%', right: '4%', size: 'text-xl md:text-3xl', rotate: '-7deg' },
  { text: 'समय सीमा', top: '78%', left: '8%', size: 'text-2xl md:text-4xl', rotate: '-6deg' },
  { text: 'निपटान', top: '72%', right: '6%', size: 'text-3xl md:text-5xl', rotate: '9deg' },
] as const;

export function WordBackdrop() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 overflow-hidden select-none"
      style={{
        maskImage:
          'radial-gradient(ellipse 58% 52% at 50% 36%, transparent 42%, black 78%)',
        WebkitMaskImage:
          'radial-gradient(ellipse 58% 52% at 50% 36%, transparent 42%, black 78%)',
      }}
    >
      {WORDS.map((word) => (
        <span
          key={`${word.text}-${word.top}`}
          className={`absolute font-hindi tracking-wide text-neutral-500 ${word.size}`}
          style={{
            top: word.top,
            left: 'left' in word ? word.left : undefined,
            right: 'right' in word ? word.right : undefined,
            transform: `rotate(${word.rotate})`,
            opacity: 0.09,
          }}
        >
          {word.text}
        </span>
      ))}
    </div>
  );
}
