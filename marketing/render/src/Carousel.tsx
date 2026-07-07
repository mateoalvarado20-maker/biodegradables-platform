import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';

export type SlideProps = {title: string; body: string};

export type CarouselProps = {
  brand_color: string;
  brand_name: string;
  cta: string;
  slides: SlideProps[];
};

// Un frame = un slide (fps 1). Se renderiza con `remotion still --frame=i`.
export const Carousel: React.FC<CarouselProps> = ({brand_color, brand_name, cta, slides}) => {
  const frame = useCurrentFrame();
  const i = Math.min(frame, Math.max(slides.length - 1, 0));
  const slide = slides[i];
  if (!slide) return null;
  const isFirst = i === 0;
  const isLast = i === slides.length - 1;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: isFirst ? brand_color : '#F6F4EE',
        padding: 90,
        justifyContent: 'space-between',
        fontFamily: 'Arial, sans-serif',
      }}
    >
      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        <div
          style={{
            fontSize: 34,
            fontWeight: 800,
            color: isFirst ? 'white' : brand_color,
            letterSpacing: 2,
            textTransform: 'uppercase',
          }}
        >
          {brand_name}
        </div>
        <div style={{fontSize: 34, fontWeight: 700, color: isFirst ? 'white' : '#666'}}>
          {i + 1}/{slides.length}
        </div>
      </div>

      <div>
        <div
          style={{
            fontFamily: 'Arial Black, Arial, sans-serif',
            fontSize: isFirst ? 110 : 76,
            fontWeight: 900,
            lineHeight: 1.12,
            color: isFirst ? 'white' : '#1a1a1a',
            marginBottom: 48,
          }}
        >
          {slide.title}
        </div>
        <div
          style={{
            fontSize: 46,
            lineHeight: 1.45,
            color: isFirst ? 'rgba(255,255,255,0.94)' : '#333',
            maxWidth: 880,
          }}
        >
          {slide.body}
        </div>
      </div>

      <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
        {isLast ? (
          <div
            style={{
              backgroundColor: brand_color,
              color: 'white',
              fontSize: 42,
              fontWeight: 800,
              padding: '26px 48px',
              borderRadius: 18,
              maxWidth: 880,
            }}
          >
            {cta}
          </div>
        ) : (
          <div style={{fontSize: 40, fontWeight: 700, color: isFirst ? 'white' : brand_color}}>
            Desliza →
          </div>
        )}
        <div
          style={{
            width: 88,
            height: 12,
            borderRadius: 6,
            backgroundColor: isFirst ? 'rgba(255,255,255,0.6)' : brand_color,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
