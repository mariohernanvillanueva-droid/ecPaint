# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'resize.ui'
##
## Created by: Qt User Interface Compiler version 6.11.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QRadioButton, QSizePolicy,
    QSpacerItem, QSpinBox, QVBoxLayout, QWidget)

class Ui_ResizeDialog(object):
    def setupUi(self, ResizeDialog):
        if not ResizeDialog.objectName():
            ResizeDialog.setObjectName(u"ResizeDialog")
        ResizeDialog.resize(320, 250)
        self.verticalLayout = QVBoxLayout(ResizeDialog)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.groupBox = QGroupBox(ResizeDialog)
        self.groupBox.setObjectName(u"groupBox")
        self.horizontalLayout = QHBoxLayout(self.groupBox)
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.radioPercentage = QRadioButton(self.groupBox)
        self.radioPercentage.setObjectName(u"radioPercentage")
        self.radioPercentage.setChecked(True)

        self.horizontalLayout.addWidget(self.radioPercentage)

        self.radioPixels = QRadioButton(self.groupBox)
        self.radioPixels.setObjectName(u"radioPixels")

        self.horizontalLayout.addWidget(self.radioPixels)


        self.verticalLayout.addWidget(self.groupBox)

        self.gridLayout = QGridLayout()
        self.gridLayout.setObjectName(u"gridLayout")
        self.label = QLabel(ResizeDialog)
        self.label.setObjectName(u"label")

        self.gridLayout.addWidget(self.label, 0, 0, 1, 1)

        self.spinHorizontal = QSpinBox(ResizeDialog)
        self.spinHorizontal.setObjectName(u"spinHorizontal")
        self.spinHorizontal.setMaximum(9999)
        self.spinHorizontal.setValue(100)

        self.gridLayout.addWidget(self.spinHorizontal, 0, 1, 1, 1)

        self.label_2 = QLabel(ResizeDialog)
        self.label_2.setObjectName(u"label_2")

        self.gridLayout.addWidget(self.label_2, 1, 0, 1, 1)

        self.spinVertical = QSpinBox(ResizeDialog)
        self.spinVertical.setObjectName(u"spinVertical")
        self.spinVertical.setMaximum(9999)
        self.spinVertical.setValue(100)

        self.gridLayout.addWidget(self.spinVertical, 1, 1, 1, 1)


        self.verticalLayout.addLayout(self.gridLayout)

        self.horizontalLayout_2 = QHBoxLayout()
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.labelMethod = QLabel(ResizeDialog)
        self.labelMethod.setObjectName(u"labelMethod")

        self.horizontalLayout_2.addWidget(self.labelMethod)

        self.comboMethod = QComboBox(ResizeDialog)
        self.comboMethod.addItem("")
        self.comboMethod.addItem("")
        self.comboMethod.setObjectName(u"comboMethod")

        self.horizontalLayout_2.addWidget(self.comboMethod)


        self.verticalLayout.addLayout(self.horizontalLayout_2)

        self.checkAspectRatio = QCheckBox(ResizeDialog)
        self.checkAspectRatio.setObjectName(u"checkAspectRatio")
        self.checkAspectRatio.setChecked(True)

        self.verticalLayout.addWidget(self.checkAspectRatio)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.verticalLayout.addItem(self.verticalSpacer)

        self.buttonBox = QDialogButtonBox(ResizeDialog)
        self.buttonBox.setObjectName(u"buttonBox")
        self.buttonBox.setOrientation(Qt.Horizontal)
        self.buttonBox.setStandardButtons(QDialogButtonBox.Cancel|QDialogButtonBox.Ok)

        self.verticalLayout.addWidget(self.buttonBox)


        self.retranslateUi(ResizeDialog)
        self.buttonBox.accepted.connect(ResizeDialog.accept)
        self.buttonBox.rejected.connect(ResizeDialog.reject)

        self.comboMethod.setCurrentIndex(1)


        QMetaObject.connectSlotsByName(ResizeDialog)
    # setupUi

    def retranslateUi(self, ResizeDialog):
        ResizeDialog.setWindowTitle(QCoreApplication.translate("ResizeDialog", u"Resize Canvas", None))
        self.groupBox.setTitle(QCoreApplication.translate("ResizeDialog", u"Resize by", None))
        self.radioPercentage.setText(QCoreApplication.translate("ResizeDialog", u"Percentage", None))
        self.radioPixels.setText(QCoreApplication.translate("ResizeDialog", u"Pixels", None))
        self.label.setText(QCoreApplication.translate("ResizeDialog", u"Horizontal:", None))
        self.label_2.setText(QCoreApplication.translate("ResizeDialog", u"Vertical:", None))
        self.labelMethod.setText(QCoreApplication.translate("ResizeDialog", u"Method:", None))
        self.comboMethod.setItemText(0, QCoreApplication.translate("ResizeDialog", u"Nearest-neighbor (Fast)", None))
        self.comboMethod.setItemText(1, QCoreApplication.translate("ResizeDialog", u"Bilinear (Smooth)", None))

        self.checkAspectRatio.setText(QCoreApplication.translate("ResizeDialog", u"Maintain aspect ratio", None))
    # retranslateUi

