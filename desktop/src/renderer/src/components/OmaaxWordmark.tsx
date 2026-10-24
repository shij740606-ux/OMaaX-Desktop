import { useId } from 'react';

/** Shared vector wordmark; neutral strokes follow the surrounding theme. */
export default function OmaaxWordmark({ className = '' }: { className?: string }) {
  const id = useId();
  const letterA = 'M54 52H43V46.8C38.1 50.4 32.5 52 26 52C11.64 52 0 40.36 0 26C0 11.64 11.64 0 26 0C41.46 0 54 11.64 54 26ZM41.9 26A15.4 15.4 0 1 0 11.1 26A15.4 15.4 0 1 0 41.9 26Z';
  return (
    <svg className={`omaax-wordmark ${className}`.trim()} viewBox="0 0 390 88" width="390" height="88" role="img" aria-label="OMaaX" focusable="false">
      <defs>
        <linearGradient id={`${id}-o`} x1="0" y1="0" x2="0" y2="88" gradientUnits="userSpaceOnUse">
          <stop stopColor="#307abd" />
          <stop offset=".5" stopColor="#7b8ca1" />
          <stop offset="1" stopColor="currentColor" />
        </linearGradient>
        <linearGradient id={`${id}-o-accent`} x1="0" y1="10" x2="0" y2="88" gradientUnits="userSpaceOnUse">
          <stop stopColor="#b46cd4" />
          <stop offset=".4" stopColor="#ba73d9" />
          <stop offset="1" stopColor="currentColor" />
        </linearGradient>
        <linearGradient id={`${id}-x`} x1="4" y1="74" x2="70" y2="0" gradientUnits="userSpaceOnUse">
          <stop stopColor="#2178c7" />
          <stop offset=".38" stopColor="#3884d0" />
          <stop offset=".7" stopColor="#9767d1" />
          <stop offset="1" stopColor="#cb75da" />
        </linearGradient>
      </defs>
      <path fill={`url(#${id}-o)`} d="M72.035 10.087A44 44 0 1 1 64.738 5.194L57.669 18.423A29 29 0 1 0 62.477 21.649Z" />
      <path fill={`url(#${id}-o-accent)`} d="M72.035 10.087A44 44 0 0 1 44 88L44 73A29 29 0 0 0 62.477 21.649Z" />
      <path fill="currentColor" transform="translate(98 8)" d="M0 0L40.5 40L81 0V80H67V32L40.5 58L14 32V80H0Z" />
      <path fill="currentColor" fillRule="evenodd" transform="translate(189 36)" d={letterA} />
      <path fill="currentColor" fillRule="evenodd" transform="translate(251 36)" d={letterA} />
      <g transform="translate(312 14)">
        <path fill={`url(#${id}-x)`} d="M0 74H22L78 0H58Z" />
        <path fill="#2f7ed1" d="M0 0H19L36 23L26 35Z" />
        <path fill="#c573db" d="M43 49L54 36L78 74H59Z" />
      </g>
    </svg>
  );
}
