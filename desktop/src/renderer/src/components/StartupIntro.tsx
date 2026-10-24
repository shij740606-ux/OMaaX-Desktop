import { useEffect, useRef, useState, type ReactNode } from 'react';
import introVideo from '../assets/omaax-startup.mp4';
import introPoster from '../assets/omaax-startup-poster.jpg';
import './StartupIntro.css';

declare global { interface Window { __OMAAX_STARTUP__?: { id: string; skipIntro: boolean } } }
const playedKey = 'omaax.startup.played';
let completedLaunch: string | undefined;
function alreadyPlayed() {
  const launch = window.__OMAAX_STARTUP__;
  if (launch?.skipIntro || completedLaunch === (launch?.id ?? 'preview')) return true;
  try { return !!launch && window.sessionStorage.getItem(playedKey) === launch.id; } catch { return false; }
}

export default function StartupIntro({ children }: { children: ReactNode }) {
  const [complete, setComplete] = useState(alreadyPlayed);
  const video = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const player = video.current;
    if (complete || !player) return;
    let alive = true;
    const finish = () => {
      if (!alive) return;
      completedLaunch = window.__OMAAX_STARTUP__?.id ?? 'preview';
      try { window.sessionStorage.setItem(playedKey, completedLaunch); } catch { /* Playback still finishes when storage is unavailable. */ }
      setComplete(true);
    };
    // A missing codec or stalled local resource must never prevent signing in.
    let watchdog = window.setTimeout(finish, 8000);
    const absoluteTimeout = window.setTimeout(finish, 15000);
    const progress = () => {
      window.clearTimeout(watchdog);
      watchdog = window.setTimeout(finish, 3000);
    };
    player.addEventListener('ended', finish);
    player.addEventListener('error', finish);
    player.addEventListener('playing', progress);
    player.addEventListener('timeupdate', progress);
    void player.play().catch(finish);
    return () => {
      alive = false;
      window.clearTimeout(watchdog);
      window.clearTimeout(absoluteTimeout);
      player.removeEventListener('ended', finish);
      player.removeEventListener('error', finish);
      player.removeEventListener('playing', progress);
      player.removeEventListener('timeupdate', progress);
      player.pause();
    };
  }, [complete]);

  if (complete) return <>{children}</>;
  return <div className="startup-intro" aria-label="OMaaX">
    <video ref={video} className="startup-intro-video" src={introVideo} poster={introPoster}
      autoPlay muted playsInline preload="auto" disablePictureInPicture aria-hidden="true" />
  </div>;
}
