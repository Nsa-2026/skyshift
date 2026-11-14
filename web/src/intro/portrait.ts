// A black silhouette with one mural-style eye, drawn as canvas paths: no image, no model, no
// generated art. Authored by 春。 with Codex on codex/frontend-unified (commit 8ab56ac).
// Coordinates are in the figure's own units; the camera in scene.ts scales and centres them.

/** The pupil, which opens onto the SPHEREx sky behind the canvas. */
export const PORTRAIT_EYE = { x: 836, y: 409, radius: 4.5 } as const;

function fill(c: CanvasRenderingContext2D, d: string, color: string) {
  c.fillStyle = color;
  c.fill(new Path2D(d));
}

export function drawPortrait(c: CanvasRenderingContext2D): void {
  const ink = "#030507";
  // A quiet frontal outline: rounded crown, blunt hair, long neck and a tapered robe.
  const head =
    "M708 544 L719 383 C719 328 748 296 797 294 C849 292 883 326 884 382 L894 544 L854 538 L838 510 Q822 529 800 532 Q778 529 761 510 L747 538Z";
  fill(
    c,
    "M777 508 L823 508 L827 558 L866 578 Q879 587 883 609 L899 722 Q896 735 886 733 L864 643 L858 619 L851 716 L866 908 Q800 921 734 908 L749 716 L742 619 L735 643 L714 733 Q705 735 701 722 L717 609 Q721 587 734 578 L773 558Z",
    ink,
  );
  fill(c, "M747 901 L774 902 L774 931 L779 944 L731 946 Q726 940 740 931Z M826 902 L853 901 L859 931 Q874 940 869 946 L821 944 L826 931Z", ink);
  fill(c, head, ink);
  // A fine edge separates the silhouette from the night without modelling a face.
  c.strokeStyle = "#91a5b52b";
  c.lineWidth = 0.65;
  c.stroke(new Path2D("M708 544 L719 383 C719 328 748 296 797 294 C849 292 883 326 884 382 L894 544"));

  // One almond eye with long liner wings, after Egyptian mural linework.
  const almond = "M811 410 Q835 390 859 409 Q837 426 811 410Z";
  fill(c, almond, "#c5c8b9");
  c.save();
  c.clip(new Path2D(almond));
  c.fillStyle = "#55645e";
  c.beginPath();
  c.arc(836, 409, 8.6, 0, Math.PI * 2);
  c.fill();
  c.strokeStyle = "#b7bba377";
  c.lineWidth = 0.55;
  c.beginPath();
  c.arc(836, 409, 7, 0, Math.PI * 2);
  c.stroke();
  c.fillStyle = ink;
  c.beginPath();
  c.arc(836, 409, PORTRAIT_EYE.radius, 0, Math.PI * 2);
  c.fill();
  c.restore();
  c.strokeStyle = "#b3ae8a";
  c.lineWidth = 0.75;
  c.stroke(new Path2D(almond));
  // A tapered outer flick and one fine lower sweep.
  fill(c, "M852 404 Q861 407 874 400 L862 412 L857 411Z", "#b3ae8a");
  c.strokeStyle = "#92947a";
  c.lineWidth = 0.65;
  c.stroke(new Path2D("M815 416 Q836 432 861 418 L871 414"));
}
