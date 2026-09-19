const FRAMES = [
  { src: '/hero/epf-loop-campus.png', alt: 'A civic campus under a clear sky' },
  { src: '/hero/epf-loop-clocktower.png', alt: 'A clock tower for watching a deadline' },
  { src: '/hero/epf-loop-sunrise.png', alt: 'Sunrise over still water' },
  { src: '/hero/epf-loop-sentinel.png', alt: 'A lighthouse standing watch' },
  { src: '/hero/epf-loop-file.png', alt: 'A claim file and pen on a desk' },
  { src: '/hero/epf-loop-deadline.png', alt: 'A desk clock and register' },
] as const;

function FrameRow() {
  return (
    <div className="flex h-full shrink-0 gap-3 pr-3">
      {FRAMES.map((frame) => (
        <figure key={frame.src} className="h-full w-[38vw] shrink-0 overflow-hidden sm:w-[22vw] md:w-[18vw]">
          <img src={frame.src} alt={frame.alt} className="h-full w-full object-cover" />
        </figure>
      ))}
    </div>
  );
}

export function ImageStrip() {
  return (
    <div className="relative mt-6 overflow-hidden" aria-hidden="true">
      <div
        className="relative h-52 sm:h-64 md:h-80"
        style={{ clipPath: 'ellipse(92% 100% at 50% 100%)' }}
      >
        <div className="absolute inset-y-0 -left-16 -right-16 sm:-left-24 sm:-right-24">
          <div className="animate-epf-marquee flex h-[120%] pointer-events-none will-change-transform">
            <FrameRow />
            <FrameRow />
          </div>
        </div>
      </div>
    </div>
  );
}
