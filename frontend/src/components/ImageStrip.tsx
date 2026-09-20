const FRAMES = [
  { src: '/hero/provident_fund_documents.jpg', alt: 'A stack of official Provident Fund documents' },
  { src: '/hero/time_tracking_clock.jpg', alt: 'A desk clock next to a financial dashboard' },
  { src: '/hero/epf_office_building.jpg', alt: 'A modern government office building' },
  { src: '/hero/digital_claim_approval.jpg', alt: 'Laptop screen displaying Claim Approved' },
  { src: '/hero/financial_security_shield.jpg', alt: 'A conceptual glass shield over banknotes' },
  { src: '/hero/calendar_deadline_marked.jpg', alt: 'A desk calendar with a circled deadline' },
] as const;

function FrameRow() {
  return (
    <div className="flex h-full shrink-0 gap-3 pr-3">
      {FRAMES.map((frame) => (
        <figure
          key={frame.src}
          className="h-full w-[30vw] shrink-0 overflow-hidden rounded-2xl sm:w-[18vw] md:w-[14vw]"
        >
          <img
            src={frame.src}
            alt={frame.alt}
            className="h-full w-full object-cover opacity-50 grayscale-20"
          />
        </figure>
      ))}
    </div>
  );
}

export function ImageStrip() {
  return (
    <div className="relative z-0 mt-auto w-full overflow-hidden pt-4" aria-hidden="true">
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-16 bg-linear-to-b from-white to-transparent" />
      <div className="relative h-36 w-full px-1 sm:h-44 md:h-48">
        <div className="absolute inset-y-0 left-0 right-0">
          <div className="animate-epf-marquee pointer-events-none flex h-full will-change-transform">
            <FrameRow />
            <FrameRow />
          </div>
        </div>
      </div>
    </div>
  );
}
