import pandas as pd
import matplotlib.pyplot as plt
import os
from scipy.stats import linregress
import numpy as np

def analyze_and_plot_pet_data(file_path: str, output_dir: str = 'vault/reports/HW5/') -> None:
    """
    Reads PET 212E data from an Excel file, renames columns, performs data cleaning,
    and generates four specific plots, saving them as PNG files.

    Args:
        file_path (str): The full path to the Excel file.
        output_dir (str): The directory where plot images will be saved.
                          Defaults to 'vault/reports/HW5/'.
    """
    # 1. Read the Excel file
    # Skip the first 3 rows (0-indexed), use the 4th row (index 3) as header
    try:
        # Row 0: title, Row 1: column headers, Row 2: units → skip rows 0 and 2
        df = pd.read_excel(file_path, skiprows=[0, 2], header=0)
    except FileNotFoundError:
        print(f"Error: The file '{file_path}' was not found.")
        return
    except Exception as e:
        print(f"Error reading Excel file: {e}")
        return

    # 2. Rename columns
    # Assuming the header row (index 3) contains these exact names
    column_rename_map = {
        'Ø': 'Atmospheric Porosity',
        'G. D.': 'Atmospheric Grain Density',
        'Ø(He)': '800psi Helium Porosity',
        'Kair': '800psi Air Permeability',
        'K¥': '800psi K_infinity Permeability'
    }

    # Identify columns that actually exist in the DataFrame before renaming
    # This handles cases where some expected columns might be missing
    existing_columns_to_rename = {
        old_name: new_name
        for old_name, new_name in column_rename_map.items()
        if old_name in df.columns
    }

    df = df.rename(columns=existing_columns_to_rename)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    print("--- Data Analysis and Plotting ---")
    print(f"Data loaded from: {file_path}")
    print(f"Output directory: {os.path.abspath(output_dir)}")
    print("\nInitial DataFrame head:")
    print(df.head())
    print("\nDataFrame columns after renaming:")
    print(df.columns.tolist())

    # --- Plotting ---

    # Common plotting parameters
    plt.style.use('seaborn-v0_8-darkgrid') # Using a modern seaborn style
    plt.rcParams.update({'font.size': 10, 'axes.labelsize': 12, 'axes.titlesize': 14})

    # List of columns that will be used for plotting
    required_cols = list(column_rename_map.values())
    
    # Drop rows with NaN values in the columns critical for plotting
    # Make a copy to avoid SettingWithCopyWarning
    df_clean = df[required_cols].dropna().copy()

    if df_clean.empty:
        print("\nWarning: No valid data points found after dropping NaN values. Skipping plots.")
        return

    print(f"\nCleaned DataFrame shape (after dropping NaNs): {df_clean.shape}")

    # Plot a: Atmospheric Porosity vs. Atmospheric Grain Density
    plt.figure(figsize=(8, 6))
    plt.scatter(df_clean['Atmospheric Grain Density'], df_clean['Atmospheric Porosity'], s=50, alpha=0.7, edgecolors='w', linewidth=0.8)
    plt.title("Atmospheric Porosity vs. Grain Density")
    plt.xlabel("Grain Density (gm/cc)")
    plt.ylabel("Porosity (%)")
    plt.grid(True, which='both', linestyle='--', linewidth=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'plot_a.png'))
    plt.close()
    print("Generated plot_a.png")

    # Plot b: 800psi Air Permeability vs. 800psi Helium Porosity (semi-log)
    plt.figure(figsize=(8, 6))
    plt.scatter(df_clean['800psi Helium Porosity'], df_clean['800psi Air Permeability'], s=50, alpha=0.7, edgecolors='w', linewidth=0.8)
    plt.title("800 psi Air Permeability vs. Helium Porosity")
    plt.xlabel("Helium Porosity (%)")
    plt.ylabel("Air Permeability (mD)")
    plt.yscale('log') # Set y-axis to log scale
    plt.grid(True, which='both', linestyle='--', linewidth=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'plot_b.png'))
    plt.close()
    print("Generated plot_b.png")

    # Plot c: 800psi Helium Porosity vs. Atmospheric Porosity
    plt.figure(figsize=(8, 6))
    plt.scatter(df_clean['Atmospheric Porosity'], df_clean['800psi Helium Porosity'], s=50, alpha=0.7, edgecolors='w', linewidth=0.8, label='Data Points')
    plt.title("800 psi Helium Porosity vs. Atmospheric Porosity")
    plt.xlabel("Atmospheric Porosity (%)")
    plt.ylabel("800 psi Helium Porosity (%)")

    # Add linear trendline
    x = df_clean['Atmospheric Porosity']
    y = df_clean['800psi Helium Porosity']
    slope, intercept, r_value, p_value, std_err = linregress(x, y)
    trendline_x = np.array([x.min(), x.max()])
    trendline_y = slope * trendline_x + intercept
    plt.plot(trendline_x, trendline_y, color='red', linestyle='--', label=f'Trendline: y={slope:.2f}x + {intercept:.2f}\n$R^2$={r_value**2:.2f}')

    # Add equality line (y=x)
    min_val = min(x.min(), y.min())
    max_val = max(x.max(), y.max())
    equality_line = np.linspace(min_val, max_val, 100)
    plt.plot(equality_line, equality_line, color='grey', linestyle=':', label='Equality Line (y=x)')

    plt.grid(True, which='both', linestyle='--', linewidth=0.5)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'plot_c.png'))
    plt.close()
    print("Generated plot_c.png")

    # Plot d: 800psi K_infinity Permeability vs. 800psi Air Permeability (log-log)
    plt.figure(figsize=(8, 6))
    plt.scatter(df_clean['800psi Air Permeability'], df_clean['800psi K_infinity Permeability'], s=50, alpha=0.7, edgecolors='w', linewidth=0.8, label='Data Points')
    plt.title("800 psi K_infinity Permeability vs. Air Permeability")
    plt.xlabel("800 psi Air Permeability (mD)")
    plt.ylabel("800 psi K_infinity Permeability (mD)")

    # Set both x and y axes to log scale
    plt.xscale('log')
    plt.yscale('log')

    # Add linear trendline (on log-transformed data for log-log plot)
    # Filter out non-positive values before log transformation if necessary,
    # as log(0) or log(negative) is undefined.
    # Assuming permeability values are positive.
    x_log = np.log10(df_clean['800psi Air Permeability'].replace(0, np.nan).dropna())
    y_log = np.log10(df_clean['800psi K_infinity Permeability'].replace(0, np.nan).dropna())

    # Ensure both log arrays have the same length after dropping NaNs
    common_indices = x_log.index.intersection(y_log.index)
    x_log_filtered = x_log.loc[common_indices]
    y_log_filtered = y_log.loc[common_indices]

    if not x_log_filtered.empty:
        slope_log, intercept_log, r_value_log, _, _ = linregress(x_log_filtered, y_log_filtered)
        
        # Trendline needs to be plotted using original scale for display clarity
        # The trendline equation is in log-log space: log10(Y) = slope * log10(X) + intercept
        # Y = 10^(intercept) * X^(slope)
        
        # Create a range for the trendline using the original (non-log) data limits
        min_air_perm = df_clean['800psi Air Permeability'].min()
        max_air_perm = df_clean['800psi Air Permeability'].max()
        trendline_x_orig = np.logspace(np.log10(min_air_perm), np.log10(max_air_perm), 100)
        trendline_y_orig = (10**intercept_log) * (trendline_x_orig**slope_log)

        plt.plot(trendline_x_orig, trendline_y_orig, color='red', linestyle='--', label=f'Trendline: $Y={10**intercept_log:.2f}X^{{{slope_log:.2f}}}$\n$R^2$={r_value_log**2:.2f}')
    else:
        print("Warning: No valid log-transformable data points for Plot D trendline.")


    # Add equality line (y=x)
    min_perm = min(df_clean['800psi Air Permeability'].min(), df_clean['800psi K_infinity Permeability'].min())
    max_perm = max(df_clean['800psi Air Permeability'].max(), df_clean['800psi K_infinity Permeability'].max())
    equality_line_log = np.logspace(np.log10(min_perm), np.log10(max_perm), 100)
    plt.plot(equality_line_log, equality_line_log, color='grey', linestyle=':', label='Equality Line (y=x)')

    plt.grid(True, which='both', linestyle='--', linewidth=0.5)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'plot_d.png'))
    plt.close()
    print("Generated plot_d.png")

# Example Usage:
if __name__ == "__main__":
    excel_file_path = r"C:\Users\mertk\OneDrive\Desktop\HW5\PET 212E_HW-5.xlsx"
    output_directory = r"vault/reports/HW5/" # Relative path, will be created in the script's working directory

    analyze_and_plot_pet_data(excel_file_path, output_directory)
