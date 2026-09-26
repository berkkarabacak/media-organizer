const shots = [
  {
    src: 'images/screenshot-main.png',
    alt: 'Media Organizer main window — source folder scan with detected capture dates',
    caption: 'The main window — scanning a source folder and reading true capture dates.',
  },
  {
    src: 'images/screenshot-preview.png',
    alt: 'Dry-run preview showing the proposed year, quarter and month folder plan',
    caption: 'The dry-run preview — the full folder plan before anything is touched.',
  },
];

export default function Screenshots() {
  return (
    <section
      id="screenshots"
      style={{ padding: '150px 5vw', background: '#0a0a0a', position: 'relative', zIndex: 2 }}
    >
      <div style={{ maxWidth: 1400, margin: '0 auto' }}>
        <div
          className="mb-6"
          style={{
            fontFamily: "'Inter', sans-serif",
            fontSize: 12,
            fontWeight: 300,
            letterSpacing: '3px',
            textTransform: 'uppercase',
            color: '#dadada',
            opacity: 0.6,
          }}
        >
          Screenshots
        </div>
        <div className="mb-16" style={{ width: '100%', height: 1, background: 'rgba(255,255,255,0.1)' }} />

        <h2
          style={{
            fontFamily: "'EB Garamond', serif",
            fontWeight: 400,
            fontSize: 'clamp(32px, 4vw, 64px)',
            lineHeight: 1.15,
            letterSpacing: '-1px',
            color: '#ffffff',
            margin: '0 0 80px 0',
            maxWidth: 700,
            textWrap: 'balance',
          }}
        >
          A dark, calm interface for a messy job.
        </h2>

        <div className="flex flex-col" style={{ gap: 80 }}>
          {shots.map((shot) => (
            <figure key={shot.src} style={{ margin: 0 }}>
              <div
                className="overflow-hidden"
                style={{
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: 12,
                  boxShadow: '0 24px 80px rgba(0,0,0,0.6)',
                }}
              >
                <img
                  src={shot.src}
                  alt={shot.alt}
                  className="w-full h-auto block"
                  loading="lazy"
                />
              </div>
              <figcaption
                style={{
                  fontFamily: "'Inter', sans-serif",
                  fontWeight: 200,
                  fontSize: 14,
                  color: '#dadada',
                  opacity: 0.6,
                  marginTop: 20,
                  textAlign: 'center',
                }}
              >
                {shot.caption}
              </figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}
