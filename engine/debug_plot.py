import matplotlib.pyplot as plt
from twin import Twin

twin = Twin.from_file("../scenarios/demo_house.json")
plt.imshow(twin.blocked, origin="lower", cmap="Greys")
plt.title("Blocked cells (grey = wall/obstacle)")
plt.savefig("debug_grid.png")
print("Saved debug_grid.png — open it to check the house shows up correctly")