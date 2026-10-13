// sampleLab.js - a ready-made example for the upload wizard: a 20 x 12 m chemistry lab drawn the way
// an architect would (public/sample-lab.png), with every point someone would click.
// It lets anyone try the feature without a plan to hand. The server-room blind spot is deliberate.

const aim = ([x, y], headingDeg, len = 100) => [
  Math.round((x + len * Math.cos((headingDeg * Math.PI) / 180)) * 10) / 10,
  Math.round((y - len * Math.sin((headingDeg * Math.PI) / 180)) * 10) / 10,
]

export const SAMPLE_LAB = {
  file: 'sample-lab.png',
  name: 'Chemistry lab (sample)',
  kind: 'indoor',
  scale: { p1: [90, 330], p2: [890, 330], metres: '20' },
  inside: [330, 510],
  entrances: [
    { name: 'Main entrance', point: [470, 570] },
    { name: 'Fire exit', point: [890, 506] },
  ],
  assets: [
    { name: 'Server room', point: [730, 190] },
    { name: 'Freezer', point: [190, 138] },
  ],
  cameras: [
    { name: 'C1', position: [110, 510], aim: aim([110, 510], 0), fov_deg: 90, range_m: 12, online: true },
    { name: 'C2', position: [110, 110], aim: aim([110, 110], 315), fov_deg: 90, range_m: 12, online: true },
    { name: 'C3', position: [870, 370], aim: aim([870, 370], 180), fov_deg: 90, range_m: 9, online: true },
  ],
}
