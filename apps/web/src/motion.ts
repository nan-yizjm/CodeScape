/** Unit-mass spring step response; time is in seconds, with no frame-rate dependency. */
export function springProgress(time: number, stiffness: number, damping: number): number {
  if (time <= 0) return 0;
  const frequency = Math.sqrt(stiffness);
  const decay = damping / 2;
  const discriminant = decay * decay - stiffness;
  if (Math.abs(discriminant) < 0.000001) {
    return 1 - Math.exp(-frequency * time) * (1 + frequency * time);
  }
  if (discriminant < 0) {
    const oscillation = Math.sqrt(-discriminant);
    return 1 - Math.exp(-decay * time) *
      (Math.cos(oscillation * time) + decay / oscillation * Math.sin(oscillation * time));
  }
  const root = Math.sqrt(discriminant);
  const slow = -decay + root;
  const fast = -decay - root;
  return 1 + (fast * Math.exp(slow * time) - slow * Math.exp(fast * time)) / (slow - fast);
}
