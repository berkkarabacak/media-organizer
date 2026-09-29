import { faqConfig } from '../config';

export default function Faq() {
  if (!faqConfig.items.length) return null;

  return (
    <section
      id="faq"
      style={{ padding: '150px 5vw', background: '#0a0a0a', position: 'relative', zIndex: 2 }}
    >
      <div style={{ maxWidth: 900, margin: '0 auto' }}>
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
          {faqConfig.kicker}
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
            margin: '0 0 64px 0',
            maxWidth: 700,
            textWrap: 'balance',
          }}
        >
          {faqConfig.heading}
        </h2>

        <div className="flex flex-col" style={{ gap: 0 }}>
          {faqConfig.items.map((item) => (
            <div
              key={item.question}
              style={{
                borderBottom: '1px solid rgba(255,255,255,0.08)',
                padding: '28px 0',
              }}
            >
              <h3
                style={{
                  fontFamily: "'Inter', sans-serif",
                  fontWeight: 400,
                  fontSize: 18,
                  color: '#ffffff',
                  margin: '0 0 12px 0',
                }}
              >
                {item.question}
              </h3>
              <p
                style={{
                  fontFamily: "'Inter', sans-serif",
                  fontWeight: 200,
                  fontSize: 14,
                  lineHeight: 1.7,
                  color: '#dadada',
                  opacity: 0.7,
                  margin: 0,
                }}
              >
                {item.answer}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
