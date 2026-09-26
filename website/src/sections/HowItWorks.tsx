const steps = [
  {
    number: '01',
    title: 'Pick a folder',
    text: 'Point Media Organizer at any messy folder — a phone dump, a camera card archive, or a whole drive of mixed photos and videos.',
  },
  {
    number: '02',
    title: 'Preview the plan',
    text: 'It reads true capture dates from EXIF and video metadata, finds duplicates, and shows the complete year/quarter/month plan. Nothing moves yet.',
  },
  {
    number: '03',
    title: 'Organize',
    text: 'Approve the plan and watch it execute with live progress. Copy or move — and every run can be undone with one click.',
  },
];

export default function HowItWorks() {
  return (
    <section
      id="how-it-works"
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
          How It Works
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
          Three steps. Zero risk.
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-3" style={{ gap: 0 }}>
          {steps.map((step, i) => (
            <div
              key={step.number}
              style={{
                padding: '40px 32px',
                borderTop: '1px solid rgba(255,255,255,0.1)',
                borderRight: i < steps.length - 1 ? '1px solid rgba(255,255,255,0.1)' : 'none',
              }}
            >
              <div
                style={{
                  fontFamily: "'Fira Code', monospace",
                  fontSize: 13,
                  color: 'rgba(200, 170, 130, 1)',
                  marginBottom: 24,
                  letterSpacing: '2px',
                }}
              >
                {step.number}
              </div>
              <h3
                style={{
                  fontFamily: "'EB Garamond', serif",
                  fontWeight: 400,
                  fontSize: 30,
                  color: '#ffffff',
                  margin: '0 0 16px 0',
                  lineHeight: 1.2,
                }}
              >
                {step.title}
              </h3>
              <p
                style={{
                  fontFamily: "'Inter', sans-serif",
                  fontWeight: 200,
                  fontSize: 15,
                  lineHeight: 1.8,
                  color: '#dadada',
                  margin: 0,
                  textWrap: 'pretty',
                }}
              >
                {step.text}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
