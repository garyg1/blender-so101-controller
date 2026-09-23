from lerobot.model.kinematics import RobotKinematics
from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
import numpy as np
import socket, time, os, threading, math
import sys
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QComboBox, 
                             QSlider, QCheckBox)
from PySide6.QtCore import Qt

class TelemetryStreamer:
    def __init__(self, args):
        self.args = args
        print(args)
        self.is_running = False
        self.thread, self.robot, self.kinematics = None, None, None
        self.current_mode = "Angle Pass-Through"
        self.current_scale = 100.0
        self.inv_x, self.inv_y, self.inv_z = False, False, False
        self.UDP_IP, self.UDP_PORT = "127.0.0.1", 5005
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.ALIGNMENT_OFFSET = np.array([
            [-1.0,  0.0,  0.0,  0.0],
            [ 0.0, -1.0,  0.0,  0.0],
            [ 0.0,  0.0,  1.0,  0.0],
            [ 0.0,  0.0,  0.0,  1.0]
        ], dtype=np.float64)

    def initialize_hardware(self):
        if self.robot: return
        print("[HARDWARE] Connecting to SO101 Leader...")
        config = SO101LeaderConfig(port=self.args.tty, id="my_so101_leader")
        self.robot = SO101Leader(config)
        self.robot.connect()
        urdf_path = "SO-ARM100/Simulation/SO101/so101_new_calib.urdf"
        self.motor_keys = [k for k in self.robot.bus.motors.keys() if k != "gripper"]
        self.kinematics = RobotKinematics(
            urdf_path=os.path.abspath(urdf_path),  
            target_frame_name="gripper_frame_link",
            joint_names=self.motor_keys
        )

    def start_streaming(self):
        if not self.is_running:
            self.initialize_hardware()
            self.is_running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()

    def stop_streaming(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None

    def _loop(self):
        try:
            while self.is_running:
                action = self.robot.get_action()
                joint_positions_deg = [float(action[k + ".pos"]) for k in self.motor_keys]
                ee_pose = self.kinematics.forward_kinematics(joint_positions_deg)
                
                if hasattr(ee_pose, "matrix"): mat = np.array(ee_pose.matrix(), dtype=np.float64)
                elif hasattr(ee_pose, "as_matrix"): mat = np.array(ee_pose.as_matrix(), dtype=np.float64)
                else: mat = np.array(ee_pose, dtype=np.float64)

                if mat.ndim == 1 and mat.size == 16: mat = mat.reshape((4, 4))
                
                # Step 1: Generate absolute target matrix in Blender's raw coordinate space
                blender_world_matrix = np.dot(mat, self.ALIGNMENT_OFFSET)
                
                # Apply distance scale multiplier to translations
                blender_world_matrix[0:3, 3] *= self.current_scale

                # If Face Origin Mode is selected, calculate pure look-at orientation from absolute positions
                if self.current_mode == "Face Origin Mode":
                    tx = blender_world_matrix[0, 3]
                    ty = blender_world_matrix[1, 3]
                    tz = blender_world_matrix[2, 3]
                    
                    f_x, f_y, f_z = -tx, -ty, -tz
                    length = math.sqrt(f_x**2 + f_y**2 + f_z**2)
                    if length > 0.0001:
                        f_x, f_y, f_z = f_x / length, f_y / length, f_z / length
                        r_x, r_y, r_z = f_y, -f_x, 0.0
                        r_len = math.sqrt(r_x**2 + r_y**2)
                        if r_len > 0.0001:
                            r_x, r_y = r_x / r_len, r_y / r_len
                            u_x, u_y, u_z = -f_z * r_y, f_z * r_x, f_x * r_y - f_y * r_x
                            
                            blender_world_matrix[0, 0], blender_world_matrix[0, 1], blender_world_matrix[0, 2] = r_x, u_x, -f_x
                            blender_world_matrix[1, 0], blender_world_matrix[1, 1], blender_world_matrix[1, 2] = r_y, u_y, -f_y
                            blender_world_matrix[2, 0], blender_world_matrix[2, 1], blender_world_matrix[2, 2] = r_z, u_z, -f_z

                # Step 2: Perform pure reflections post-calculation (Reflecting both position and basis vectors)
                # We build a standard geometric Reflection Matrix based on checked UI options
                reflection_matrix = np.identity(4, dtype=np.float64)
                if self.inv_x: reflection_matrix[0, 0] = -1.0
                if self.inv_y: reflection_matrix[1, 1] = -1.0
                if self.inv_z: reflection_matrix[2, 2] = -1.0

                # Pre-multiplying by the reflection matrix cleanly mirrors the entire workspace frame 
                # across Blender's absolute origin axes safely without warping orientation math
                final_matrix = np.dot(reflection_matrix, blender_world_matrix)

                if args.debug:
                    print(final_matrix)

                message = ",".join(f"{val:.6f}" for val in final_matrix.flatten())
                self.sock.sendto(bytes(message, "utf-8"), (self.UDP_IP, self.UDP_PORT))
                time.sleep(0.08)
        except Exception as e:
            print(f"[STREAM ERROR] {e}")
            self.is_running = False

    def cleanup(self):
        self.stop_streaming()
        if self.robot and hasattr(self.robot, "disconnect"): self.robot.disconnect()

class AppWindow(QMainWindow):
    def __init__(self, streamer):
        super().__init__()
        self.streamer = streamer
        self.setWindowTitle("SO101 Control Panel")
        
        # UI Scales automatically based on OS DPI settings, but we can set default sizing
        self.setMinimumSize(350, 280)

        # Central Widget & Layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # --- UI ELEMENTS ---
        # Toggle Button
        self.btn_toggle = QPushButton("START STREAM")
        self.btn_toggle.clicked.connect(self.toggle_action)
        layout.addWidget(self.btn_toggle)

        # Tracking Mode Dropdown
        layout.addWidget(QLabel("Tracking Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["Angle Pass-Through", "Face Origin Mode"])
        self.combo_mode.currentTextChanged.connect(self.update_mode)
        layout.addWidget(self.combo_mode)

        # Distance Scale Slider
        self.lbl_scale = QLabel("Distance Scale: 100.0x")
        layout.addWidget(self.lbl_scale)
        
        self.slider_scale = QSlider(Qt.Horizontal)
        self.slider_scale.setRange(1, 250)
        self.slider_scale.setValue(100)
        self.slider_scale.valueChanged.connect(self.update_scale)
        layout.addWidget(self.slider_scale)

        # Invert Axes Checkboxes
        inv_frame = QWidget()
        inv_layout = QHBoxLayout(inv_frame)
        inv_layout.setContentsMargins(0, 0, 0, 0)
        
        inv_layout.addWidget(QLabel("Invert Axes:"))
        
        self.check_x = QCheckBox("X")
        self.check_y = QCheckBox("Y")
        self.check_z = QCheckBox("Z")
        
        for cb in (self.check_x, self.check_y, self.check_z):
            cb.stateChanged.connect(self.update_inversions)
            inv_layout.addWidget(cb)
            
        layout.addWidget(inv_frame)

    def toggle_action(self):
        if self.streamer.is_running:
            self.streamer.stop_streaming()
            self.btn_toggle.setText("START STREAM")
            self.btn_toggle.repaint()  # Forces immediate visual redraw
        else:
            self.btn_toggle.setText("STOP STREAM")
            self.btn_toggle.repaint()  # Forces immediate visual redraw
            self.update_mode()
            self.update_scale()
            self.update_inversions()
            self.streamer.start_streaming()


    def update_mode(self):
        self.streamer.current_mode = self.combo_mode.currentText()

    def update_scale(self):
        v = float(self.slider_scale.value())
        self.lbl_scale.setText(f"Distance Scale: {v:.1f}x")
        self.streamer.current_scale = v

    def update_inversions(self):
        self.streamer.inv_x = self.check_x.isChecked()
        self.streamer.inv_y = self.check_y.isChecked()
        self.streamer.inv_z = self.check_z.isChecked()

    def closeEvent(self, event):
        self.streamer.cleanup()
        event.accept()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('tty')
    ap.add_argument('-d', '--debug', action='store_true')
    args = ap.parse_args()
    streamer = TelemetryStreamer(args)

    # Enforce standard crisp rendering on modern High-DPI displays
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    window = AppWindow(streamer)
    window.show()
    sys.exit(app.exec())