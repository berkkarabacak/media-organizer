import { pricingConfig } from '../config';

export default function Pricing() {
  if (!pricingConfig.tiers.length) return null;

  return (
    <section
      id="pricing"
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
          {pricingConfig.kicker}
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
          {pricingConfig.heading}
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2" style={{ gap: 32, maxWidth: 900 }}>
          {pricingConfig.tiers.map((tier) => (
            <div
              key={tier.name}
              style={{
                border: `1px solid ${tier.highlighted ? 'rgba(245, 180, 90, 0.5)' : 'rgba(255,255,255,0.1)'}`,
                borderRadius: 12,
                padding: 40,
                background: tier.highlighted ? 'rgba(245, 180, 90, 0.05)' : 'rgba(255,255,255,0.02)',
                display: 'flex',
                flexDirection: 'column',
                gap: 24,
              }}
            >
              <div>
                <div
                  style={{
                    fontFamily: "'Inter', sans-serif",
                    fontSize: 12,
                    fontWeight: 300,
                    letterSpacing: '2px',
                    textTransform: 'uppercase',
                    color: '#dadada',
                    opacity: 0.6,
                    marginBottom: 16,
                  }}
                >
                  {tier.name}
                </div>
                <div
                  style={{
                    fontFamily: "'EB Garamond', serif",
                    fontSize: 56,
                    fontWeight: 400,
                    color: '#ffffff',
                    lineHeight: 1,
                  }}
                >
                  {tier.price}
                </div>
                <div
                  style={{
                    fontFamily: "'Inter', sans-serif",
                    fontWeight: 200,
                    fontSize: 14,
                    color: '#dadada',
                    opacity: 0.6,
                    marginTop: 8,
                  }}
                >
                  {tier.tagline}
                </div>
              </div>

              <ul style={{ listStyle: 'none', margin: 0, padding: 0, flex: 1 }}>
                {tier.features.map((f) => (
                  <li
                    key={f}
                    style={{
                      fontFamily: "'Inter', sans-serif",
                      fontWeight: 200,
                      fontSize: 14,
                      color: '#dadada',
                      padding: '8px 0',
                      borderBottom: '1px solid rgba(255,255,255,0.06)',
                    }}
                  >
                    <span style={{ color: '#f5b45a', marginRight: 10 }}>›</span>
                    {f}
                  </li>
                ))}
              </ul>

              <a
                href={tier.ctaHref}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  fontFamily: "'GeistMono', monospace",
                  fontSize: 14,
                  textAlign: 'center',
                  textDecoration: 'none',
                  padding: '14px 24px',
                  borderRadius: 8,
                  color: tier.highlighted ? '#0a0a0a' : '#ffffff',
                  background: tier.highlighted ? '#f5b45a' : 'transparent',
                  border: tier.highlighted ? 'none' : '1px solid rgba(255,255,255,0.25)',
                  transition: 'opacity 0.2s',
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.opacity = '0.85'; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.opacity = '1'; }}
              >
                {tier.ctaText}
              </a>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
