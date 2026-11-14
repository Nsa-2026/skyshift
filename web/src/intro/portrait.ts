/** Procedural frontal head: a lit 3-D height surface, authored hair curves and vector eyes.
 * Generated in memory by the running tool; no portrait images, video, or AI assets. */
export const PORTRAIT_EYE = { x: 836, y: 409, radius: 4.5 };
let skin: HTMLCanvasElement | undefined;
const bell = (value: number, center: number, spread: number) => Math.exp(-(((value - center) / spread) ** 2));

function depth(x: number, y: number): number {
  const ny = (y - 411) / 105;
  const width = 82 * (ny > 0 ? 1 - .23 * ny : 1);
  const sphere = 1 - (x / width) ** 2 - ny ** 2;
  if (sphere <= 0) return NaN;
  return Math.sqrt(sphere) * 60
    + 8 * bell(x, 0, 15) * bell(y, 429, 39)
    + 9 * bell(x, 0, 16) * bell(y, 451, 11)
    + 4 * (bell(x, -46, 22) + bell(x, 46, 22)) * bell(y, 445, 24)
    - 8 * (bell(x, -36, 20) + bell(x, 36, 20)) * bell(y, 411, 14)
    - 3 * bell(x, 0, 23) * bell(y, 474, 5);
}

function skinSurface(): HTMLCanvasElement {
  if (skin) return skin;
  skin = document.createElement('canvas'); skin.width = 660; skin.height = 864;
  const c = skin.getContext('2d')!;
  const data = c.createImageData(skin.width, skin.height);
  for (let py = 0; py < skin.height; py++) {
    const y = 302 + py / 4;
    for (let px = 0; px < skin.width; px++) {
      const x = -82.5 + px / 4;
      const z = depth(x, y);
      if (!Number.isFinite(z)) continue;
      const dx = (depth(x + .15, y) - depth(x - .15, y)) / .3;
      const dy = (depth(x, y + .15) - depth(x, y - .15)) / .3;
      if (!Number.isFinite(dx + dy)) continue;
      const norm = Math.hypot(dx, dy, 1);
      const diffuse = Math.max(0, (-dx * -.45 - dy * -.50 + .74) / norm);
      const rim = Math.pow(Math.min(1, Math.abs(x) / 78), 7) * .27;
      const blush = (bell(x, -44, 25) + bell(x, 44, 25)) * bell(y, 452, 22);
      const sheen = Math.pow(Math.max(0, (-dx * -.2 - dy * -.25 + .94) / norm), 20) * 22;
      const light = .43 + .55 * diffuse;
      const i = (py * skin.width + px) * 4;
      data.data.set([
        Math.min(255, 172 * light + 20 * blush + sheen + rim * 70),
        Math.min(255, 143 * light + 4 * blush + sheen + rim * 105),
        Math.min(255, 131 * light + 12 + sheen + rim * 155),
        255,
      ], i);
    }
  }
  c.putImageData(data, 0, 0);
  return skin;
}

function path(c: CanvasRenderingContext2D, d: string, fill: string | CanvasGradient) {
  c.fillStyle = fill; c.fill(new Path2D(d));
}
function eye(c: CanvasRenderingContext2D, center: number, side: number, glint: number) {
  const x = center;
  // Upper lid arcs up; the slight upward gaze keeps the pupils above the lower lid.
  const outline = `M${x - 23} 415 Q${x - 6} 399 ${x + 12} 406 Q${x + 20} 410 ${x + 23} 415 Q${x + 2} 425 ${x - 23} 415Z`;
  const white = c.createLinearGradient(x, 402, x, 425);
  white.addColorStop(0, '#526373'); white.addColorStop(.5, '#b0b6b2'); white.addColorStop(1, '#65788b');
  c.save(); c.clip(new Path2D(outline));
  path(c, outline, white);
  const iris = c.createRadialGradient(x, 409, 2, x, 409, 9.5);
  iris.addColorStop(0, '#263031'); iris.addColorStop(.55, '#64736d'); iris.addColorStop(.85, '#44595d'); iris.addColorStop(1, '#182d38');
  c.fillStyle = iris; c.beginPath(); c.arc(x, 409, 9.5, 0, Math.PI * 2); c.fill();
  for (let i = 0; i < 84; i++) {
    const a = i * Math.PI * 2 / 84;
    const r = 7.9 + Math.sin(i * 2.3) * .8;
    c.strokeStyle = i % 3 ? '#a1a28270' : '#24414d99'; c.lineWidth = .22;
    c.beginPath(); c.moveTo(x + Math.cos(a) * 4.8, 409 + Math.sin(a) * 4.8);
    c.quadraticCurveTo(x + Math.cos(a + .04) * 7, 409 + Math.sin(a + .04) * 7, x + Math.cos(a) * r, 409 + Math.sin(a) * r); c.stroke();
  }
  c.fillStyle = '#030811'; c.beginPath(); c.arc(x, 409, 4.5, 0, Math.PI * 2); c.fill();
  if (glint > 0) { c.fillStyle = `rgba(222,239,247,${glint * .8})`; c.beginPath(); c.ellipse(x - 3, 405, 1.6, 1, -.5, 0, Math.PI * 2); c.fill(); }
  c.restore();
  c.strokeStyle = '#18202be6'; c.lineWidth = 1.2;
  c.stroke(new Path2D(`M${x - 23} 415 Q${x - 6} 399 ${x + 12} 406 Q${x + 20} 410 ${x + 23} 415`));
  c.strokeStyle = '#a3a7a266'; c.lineWidth = .5;
  c.stroke(new Path2D(`M${x - 22} 416 Q${x + 1} 426 ${x + 23} 416`));
  c.strokeStyle = '#34404ecc'; c.lineWidth = .6;
  for (let j = 0; j < 8; j++) {
    const at = x + side * (11 + j * 1.35);
    c.beginPath(); c.moveTo(at, 407 + j * .7); c.lineTo(at + side * 2.4, 404 + j * .85); c.stroke();
  }
  // Individual eyebrow hairs, rather than a heavy graphic band.
  c.strokeStyle = '#29303c77';
  for (let j = 0; j < 36; j++) {
    const xx = x - 23 + j * 1.3 + Math.sin(j * 2.6) * .25;
    const yy = 394 - Math.sin(j / 36 * Math.PI) * 4.4;
    c.lineWidth = .4 + (1 - j / 36) * .25;
    c.beginPath(); c.moveTo(xx, yy + 2); c.quadraticCurveTo(xx + side, yy - .5, xx + 1.5 * side, yy - 1.1 - Math.sin(j * 1.8) * .6); c.stroke();
  }
}

export function drawPortrait(c: CanvasRenderingContext2D, glint = 1): void {
  const hair = c.createLinearGradient(690, 370, 925, 500);
  hair.addColorStop(0, '#111b2b'); hair.addColorStop(.3, '#1d2434'); hair.addColorStop(.7, '#080e19'); hair.addColorStop(1, '#1b2a3b');
  path(c, 'M718 414 C691 349 720 300 777 292 C850 276 900 318 897 390 C899 434 913 471 913 521 C909 577 942 604 917 650 C889 681 850 629 814 637 C762 657 685 652 690 596 C704 541 687 484 718 414Z', hair);
  const neck = c.createLinearGradient(770, 477, 822, 563);
  neck.addColorStop(0, '#323746'); neck.addColorStop(.6, '#6e6b70'); neck.addColorStop(1, '#252c3c');
  path(c, 'M769 478 L831 477 L837 535 L859 551 Q801 585 744 550 L766 532Z', neck);
  const cloth = c.createLinearGradient(691, 550, 899, 730);
  cloth.addColorStop(0, '#263449'); cloth.addColorStop(.4, '#182437'); cloth.addColorStop(1, '#0a1322');
  path(c, 'M754 534 Q730 539 713 555 L681 647 L670 734 Q675 747 688 738 L716 662 L730 625 L725 736 Q801 759 873 736 L870 626 L889 674 L909 740 Q923 744 925 732 L914 649 L890 564 Q875 546 842 536 Q803 567 754 534Z', cloth);
  path(c, 'M727 727 L871 729 L872 816 L857 931 L822 931 L811 824 L798 785 L784 828 L773 931 L738 931 L727 819Z', '#0a1220');
  path(c, 'M735 923 L776 923 L775 945 L721 946 Q716 936 735 923Z M823 923 L858 923 L876 941 Q877 946 868 946 L822 943Z', '#050b14');
  c.strokeStyle = '#7c8caa38'; c.lineWidth = 1.2;
  c.stroke(new Path2D('M753 543 Q797 579 844 544 M724 577 L713 649 M870 577 L895 673 M746 745 L746 815 M812 745 L813 811'));
  path(c, 'M670 731 Q666 746 672 754 Q683 764 688 750 L688 734Z M908 733 L909 752 Q914 763 923 751 L925 733Z', '#49505e');
  // Ears and the smoothly shaded parametric surface.
  const ears = c.createLinearGradient(710, 400, 890, 430);
  ears.addColorStop(0, '#5d5963'); ears.addColorStop(.5, '#857e7e'); ears.addColorStop(1, '#35414f');
  c.fillStyle = ears; c.beginPath(); c.ellipse(723, 424, 12, 22, -.2, 0, Math.PI * 2); c.fill(); c.beginPath(); c.ellipse(877, 424, 12, 22, .2, 0, Math.PI * 2); c.fill();
  c.drawImage(skinSurface(), 717.5, 302, 165, 216);
  // Nose and lips are restrained so the zoom reads as a face before it becomes an eye.
  c.strokeStyle = '#332c3555'; c.lineWidth = 1.1;
  c.stroke(new Path2D('M788 450 Q786 455 792 457 M808 457 Q814 455 811 450'));
  const lips = c.createLinearGradient(800, 467, 800, 482);
  lips.addColorStop(0, '#775c65'); lips.addColorStop(.4, '#463f4c'); lips.addColorStop(.55, '#8a7279'); lips.addColorStop(1, '#695a68');
  path(c, 'M778 474 Q789 472 795 469 Q799 472 802 469 Q811 472 822 474 Q812 484 800 483 Q787 483 778 474Z', lips);
  c.strokeStyle = '#433a485f'; c.lineWidth = .7; c.stroke(new Path2D('M778 474 Q800 477 822 474'));
  eye(c, 764, -1, glint); eye(c, 836, 1, glint);
  // A softly parted fringe and long strands frame the frontal face.
  path(c, 'M716 422 C699 364 717 310 771 299 Q816 283 849 308 C814 308 790 324 777 344 Q755 368 728 373 L726 430Z', hair);
  path(c, 'M810 306 C863 285 896 336 888 395 L882 455 Q872 416 871 373 Q863 341 840 329Z', hair);
  for (let i = 0; i < 27; i++) {
    const dx = i * 1.15;
    c.strokeStyle = i % 4 ? '#71819a12' : '#a0adbb21'; c.lineWidth = .55;
    c.stroke(new Path2D(`M${774 - dx} ${306 + i * .4} C${693 - dx * .15} 349 ${731 - dx * .55} 422 ${715 - dx * .3} 478 C${693 - dx * .6} 532 ${746 - dx} 569 ${718 - dx * .4} 617`));
    if (i < 19) c.stroke(new Path2D(`M${834 + dx} ${312 + i} C${897 + dx * .3} 369 ${869 + dx * .5} 438 ${891 + dx * .4} 497 Q${923 + dx * .2} 558 ${887 + dx * .4} 610`));
  }
}
