var CR_All;

var vl = {
    Table: 0,
    Key: '',
    Text: '',
    Oper: '',
    DtStart: '',
    DtEnd: ''
};

function ChangeStatus(txt, st) {
    Select('Отправить '+txt+' на проверку?', function () {
        Send('Teacher/PersonalPlanChangeStatus', st, function (res) {
            Ajax_Show();
            window.location.reload();
        });
    });
}


//                      0                   1                       2                               3                    4                           5                          6
var StatusText = ['Заполнение плана', 'План на проверке', 'План утвержден. Заполнение отчёта', 'План отклонён', 'Отчет на проверке', 'Отчет утвержден', 'Отчет отклонён'];

$(function () {


    $('.status-text').html(StatusText[StatusPlan]);
    if ($('#IdEmpStaff option').length > 1) {
        $('#IdEmpStaff').show().change(function () {
            Ajax_Show();
            window.location = '/Teacher/'+Go+'/' + $(this).val();
        });
        $('.CurrentRole').after($('#IdEmpStaff'));
    }
    else {
        $('.CurrentRole').after($('<span class="staff-one">' + $('#IdEmpStaff option').eq(0).text() + '</span>'));
    }

    INIT_UR();
    INIT_UR_ALL();
    INIT_VAR();


    switch (StatusPlan) {
        case 0:// нормальное редактирование
        case 3:
            EDIT_UR();
            EDIT_UR_ALL_SMALL();
            EDIT_VAR();
            $('.status-change-0').show();
            StatusPlan = 0;
            break;
        case 2:
        case 6:
            RESULT_UR();
            RESULT_UR_ALL();
            RESULT_VAR();
            $('.status-change-2').show();
            StatusPlan=2
            break;
        case 1:     //ничего нельзя
        case 4:
        case 5:
            break;
    }
    for (var k = 0; k <= StatusPlan; k++) $('.time-line-content div[vl=' + k + ']').addClass('now');
    //$('.time-line-content div[vl=' + StatusPlan + ']').addClass('now');

    $('.left-tabs').TabsList(function (vlt, obj) {
        $('.vs').hide();
        $('.vs-' + vlt).show();
        $('.vst-' + vlt).show();
        vl.Table = parseInt(obj.attr('tb'));
        if (vl.Table == 30) {
            Send('Teacher/PersonalPlanTable4', 0, function (res) {
                $('.res_4').html(res.Title);
            });
        }
    });
    $('.toolbar-menu').ToolBarMdi([
{
    title: '', cl: 'file-excel', func: function () {
        Send('Teacher/PersonalPlanExcel', 0, function () { });
    }
}]);

});


/*********************************************************************************************/
/*                  Utility                                                               */
/*********************************************************************************************/
function int(vl) {
    var tt = parseFloat(vl.replace(',', '.'));
    if (isNaN(tt)) tt = parseFloat(vl.replace('.',','));
    return (isNaN(tt) == false) ? tt : 0;
}

function TblDel(obj) {
    vl.Key = obj.attr('key');
    vl.Oper = 'del';
    SendModel('Teacher/PersonalPlanSaveTable', vl, function (res) {
        obj.remove();
    });
}
