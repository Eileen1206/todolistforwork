from django import forms

from .models import Client, RecurringTask, Task


class PasteForm(forms.Form):
    text = forms.CharField(
        label="從 notepad 貼上",
        widget=forms.Textarea(attrs={"rows": 18, "spellcheck": "false", "autofocus": True,
                                     "placeholder": "CAL todo\n\n10/20\n- 6400 reimage upgrade\n- 看3950要納管在哪台SMS\n\n10/28(3950 & 9200 online)\n- crontab ..."}),
    )
    default_client = forms.CharField(
        label="預設客戶（文字裡沒寫「XXX todo」時用）", required=False, max_length=60,
        widget=forms.TextInput(attrs={"list": "client-list", "placeholder": "例如 CAL"}),
    )


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "client", "due_date", "is_milestone", "notes", "done"]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 5, "spellcheck": "false"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(is_active=True)
        self.fields["client"].empty_label = "（不分客戶）"


class RecurringForm(forms.ModelForm):
    class Meta:
        model = RecurringTask
        fields = ["title", "client", "day_of_month", "start_date", "notes", "active"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 4, "spellcheck": "false"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client"].queryset = Client.objects.filter(is_active=True)


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "gcal_color", "is_active"]
