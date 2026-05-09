import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Load data
ambient_data = pd.read_excel('C:/Users/mertk/OneDrive/Desktop/HW5/PET 212E_HW-5.xlsx', sheet_name='Ambient data')

# Plot: Ø−ρ (grain density) at Atmospheric conditions.
plt.scatter(ambient_data['Ø'], ambient_data['G. D.'])
plt.xlabel('Ø')
plt.ylabel('ρ (grain density)')
plt.title('Ø vs ρ (grain density) at Atmospheric conditions')
plt.show()

# Plot: k −Ø both measured at 800 psi on semi-log.
plt.semilogx(ambient_data['Ø'], ambient_data['Kair'])
plt.xlabel('Ø')
plt.ylabel('k (air)')
plt.title('k vs Ø at 800 psi')
plt.show()

# Plot: Ø −Ø . Add trendline and Eq with R2 (correlation coefficient).
# 800 Ø
plt.scatter(ambient_data['Ø'], ambient_data['Ø_800'])
plt.xlabel('Ø')
plt.ylabel('Ø_800')
plt.title('Ø vs Ø_800')
plt.show()

# Plot: k −k measured at 800 psi on log-log scale. Add trendline and Eq
# ∞ Ø ∞ air
with R2 (correlation coefficient). Construct the Equality line passing minimum and maximum values.
plt.loglog(ambient_data['Kair'], ambient_data['Kinfty'])
plt.xlabel('k (air)')
plt.ylabel('k (infty)')
plt.title('k vs k at 800 psi')
plt.show()